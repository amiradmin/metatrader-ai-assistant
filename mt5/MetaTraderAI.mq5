// MetaTraderAI risk-profile wrapper.
// LOW/MEDIUM preserve the strict confirmation path. HIGH is an intentionally
// aggressive DEMO-only stress-test profile. Real/contest order placement is
// still HARD BLOCKED.

#define OnInit MetaTraderAICore_OnInit
#define OnTick MetaTraderAICore_OnTick
#define OnTimer MetaTraderAICore_OnTimer
#define OnDeinit MetaTraderAICore_OnDeinit
#include "MetaTraderAI_Core.mqh"
#undef OnInit
#undef OnTick
#undef OnTimer
#undef OnDeinit

#include "NYSessionTracker.mqh"

enum ENUM_MT_AI_RISK_MODE
{
   MT_AI_LOW = 0,
   MT_AI_MEDIUM = 1,
   MT_AI_HIGH = 2
};

// HIGH DEMO is deliberately extreme for stress testing:
// - DEFAULT profile
// - confidence floor 45
// - 5-ticket basket on hedging accounts
// - target 5% equity stop-risk PER ticket when sizing permits
// - broker minimum-lot fallback is allowed only in HIGH DEMO
// - actual projected basket stop-risk must remain below the 35% daily ceiling
// - API WAIT can still become a direction from technical_score
// - MTF / anti-chase / pullback / rejection confirmation are bypassed in HIGH
// - real/contest accounts, HIGH-impact news and extreme spread remain hard stops
input ENUM_MT_AI_RISK_MODE RiskMode = MT_AI_HIGH;
input bool RequireEntryConfirmation = true;

string RiskProfilePrefix = "MetaTraderAI_RiskProfile_";
ulong NYWrapperLastObservedSignalMs = 0;

string RiskModeName()
{
   if(RiskMode == MT_AI_HIGH) return "HIGH DEMO";
   if(RiskMode == MT_AI_MEDIUM) return "MEDIUM";
   return "LOW";
}

int RiskModeMinConfidence()
{
   if(RiskMode == MT_AI_HIGH) return 45;
   if(RiskMode == MT_AI_MEDIUM) return 78;
   return 82;
}

double RiskModePerOrderRiskPercent()
{
   if(RiskMode == MT_AI_HIGH) return 5.00;
   if(RiskMode == MT_AI_MEDIUM) return 0.25;
   return 0.15;
}

int RiskModeRequestedPositions()
{
   if(RiskMode == MT_AI_HIGH) return 5;
   if(RiskMode == MT_AI_MEDIUM) return 2;
   return 1;
}

double RiskModeDailyLossLimitPercent()
{
   if(RiskMode == MT_AI_HIGH) return 35.0;
   if(RiskMode == MT_AI_MEDIUM) return 2.5;
   return 1.5;
}

int RiskModeMaxSpreadPoints()
{
   if(RiskMode == MT_AI_HIGH) return 150;
   if(RiskMode == MT_AI_MEDIUM) return 50;
   return 35;
}

bool IsHedgingAccount()
{
   ENUM_ACCOUNT_MARGIN_MODE mode =
      (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   return mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
}

int RiskModeEffectivePositions()
{
   int requested = RiskModeRequestedPositions();
   if(requested > 1 && !IsHedgingAccount())
      return 1;
   return requested;
}

color RiskModeColor()
{
   if(RiskMode == MT_AI_HIGH) return clrTomato;
   if(RiskMode == MT_AI_MEDIUM) return clrGold;
   return clrLime;
}

void DrawRiskModeBadge()
{
   string name = RiskProfilePrefix + "Mode";
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, PanelLeft + 390);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, PanelTop + 18);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, MathMax(10, PanelFontSize - 2));
   ObjectSetInteger(0, name, OBJPROP_COLOR, RiskModeColor());
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetString(
      0,
      name,
      OBJPROP_TEXT,
      "MODE: " + RiskModeName() +
      " | C" + IntegerToString(RiskModeMinConfidence()) +
      " | x" + IntegerToString(RiskModeEffectivePositions()) +
      " | " + DoubleToString(RiskModePerOrderRiskPercent(), 2) + "% target each"
   );
}

void DeleteRiskModeBadge()
{
   ObjectDelete(0, RiskProfilePrefix + "Mode");
}

bool ProfileDailyRiskAllows(const int additional_positions)
{
   double realized_pnl = 0.0;
   double day_start_balance = 0.0;
   if(!AccountDayRiskMetrics(realized_pnl, day_start_balance))
      return false;
   if(day_start_balance <= 0.0)
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double drawdown = MathMax(
      0.0,
      (day_start_balance - equity) / day_start_balance * 100.0
   );
   double projected =
      drawdown + RiskModePerOrderRiskPercent() * MathMax(0, additional_positions);

   if(projected > RiskModeDailyLossLimitPercent())
   {
      if(Verbose)
      {
         Print(
            "MetaTraderAI profile gate: projected nominal risk ",
            DoubleToString(projected, 2),
            "% > ", DoubleToString(RiskModeDailyLossLimitPercent(), 2), "%"
         );
      }
      return false;
   }
   return true;
}

bool ManagedOpenStopRiskMoney(double &risk_money)
{
   risk_money = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != TradeSymbol)
         continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      double volume = PositionGetDouble(POSITION_VOLUME);
      double current_price = PositionGetDouble(POSITION_PRICE_CURRENT);
      double stop = PositionGetDouble(POSITION_SL);
      if(volume <= 0.0 || current_price <= 0.0 || stop <= 0.0)
      {
         if(Verbose)
            Print("MetaTraderAI HIGH DEMO: unable to measure existing stop risk.");
         return false;
      }

      ENUM_POSITION_TYPE position_type =
         (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      ENUM_ORDER_TYPE order_type =
         position_type == POSITION_TYPE_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

      double stop_profit = 0.0;
      if(!OrderCalcProfit(
         order_type, TradeSymbol, volume, current_price, stop, stop_profit
      ))
      {
         if(Verbose)
            Print("MetaTraderAI HIGH DEMO: OrderCalcProfit failed on open stop risk.");
         return false;
      }

      // If SL is already beyond break-even there is no remaining downside risk.
      risk_money += MathMax(0.0, -stop_profit);
   }

   return true;
}

bool ActualBasketRiskAllows(
   const double new_order_risk_money,
   const int new_positions,
   const double requested_volume,
   const double actual_volume
)
{
   double realized_pnl = 0.0;
   double day_start_balance = 0.0;
   if(!AccountDayRiskMetrics(realized_pnl, day_start_balance))
   {
      if(Verbose)
         Print("MetaTraderAI HIGH DEMO blocked: day-risk telemetry unavailable.");
      return false;
   }
   if(day_start_balance <= 0.0 || new_order_risk_money <= 0.0 || new_positions <= 0)
      return false;

   double existing_open_risk_money = 0.0;
   if(!ManagedOpenStopRiskMoney(existing_open_risk_money))
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double drawdown_percent = MathMax(
      0.0,
      (day_start_balance - equity) / day_start_balance * 100.0
   );
   double basket_risk_money = new_order_risk_money * new_positions;
   double basket_risk_percent = basket_risk_money / day_start_balance * 100.0;
   double existing_risk_percent = existing_open_risk_money / day_start_balance * 100.0;
   double projected_percent =
      drawdown_percent + existing_risk_percent + basket_risk_percent;

   if(projected_percent > RiskModeDailyLossLimitPercent())
   {
      Print(
         "MetaTraderAI HIGH DEMO BLOCKED: requested volume=",
         DoubleToString(requested_volume, 4),
         " broker min=",
         DoubleToString(SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN), 4),
         " actual volume=", DoubleToString(actual_volume, 4),
         " risk/order=$", DoubleToString(new_order_risk_money, 2),
         " basket risk=", DoubleToString(basket_risk_percent, 2), "%",
         " existing stop risk=", DoubleToString(existing_risk_percent, 2), "%",
         " daily DD=", DoubleToString(drawdown_percent, 2), "%",
         " projected=", DoubleToString(projected_percent, 2), "% > ",
         DoubleToString(RiskModeDailyLossLimitPercent(), 2), "%"
      );
      return false;
   }

   if(Verbose)
   {
      Print(
         "MetaTraderAI HIGH DEMO basket preflight OK: requested volume=",
         DoubleToString(requested_volume, 4),
         " actual volume=", DoubleToString(actual_volume, 4),
         " risk/order=$", DoubleToString(new_order_risk_money, 2),
         " x", new_positions,
         " basket risk=", DoubleToString(basket_risk_percent, 2), "%",
         " existing stop risk=", DoubleToString(existing_risk_percent, 2), "%",
         " daily DD=", DoubleToString(drawdown_percent, 2), "%",
         " projected=", DoubleToString(projected_percent, 2), "% / ",
         DoubleToString(RiskModeDailyLossLimitPercent(), 2), "%"
      );
   }
   return true;
}

bool EntryCandleConfirmed(const string action)
{
   if(!RequireEntryConfirmation)
      return true;

   double open1 = iOpen(TradeSymbol, PERIOD_M15, 1);
   double high1 = iHigh(TradeSymbol, PERIOD_M15, 1);
   double low1 = iLow(TradeSymbol, PERIOD_M15, 1);
   double close1 = iClose(TradeSymbol, PERIOD_M15, 1);
   double close2 = iClose(TradeSymbol, PERIOD_M15, 2);
   if(open1 <= 0.0 || high1 <= 0.0 || low1 <= 0.0 || close1 <= 0.0 || close2 <= 0.0)
      return false;

   double ema9 = 0.0;
   if(!GetCompletedIndicatorValue(Ema9Handle, ema9))
      return false;

   double body = MathAbs(close1 - open1);
   double range = high1 - low1;
   if(range <= 0.0)
      return false;

   if(action == "BUY")
   {
      double lower_wick = MathMin(open1, close1) - low1;
      bool impulse = body / range >= 0.55;
      bool rejection = lower_wick >= MathMax(body * 0.35, range * 0.15);
      return close1 > open1 && close1 > close2 && close1 >= ema9 && (impulse || rejection);
   }

   double upper_wick = high1 - MathMax(open1, close1);
   bool impulse = body / range >= 0.55;
   bool rejection = upper_wick >= MathMax(body * 0.35, range * 0.15);
   return close1 < open1 && close1 < close2 && close1 <= ema9 && (impulse || rejection);
}

string HighDemoDirection(const string json)
{
   string api_action = JsonValue(json, "action");
   if(api_action == "BUY" || api_action == "SELL")
      return api_action;

   int technical_score = (int)StringToInteger(JsonValue(json, "technical_score"));
   if(technical_score > 0) return "BUY";
   if(technical_score < 0) return "SELL";

   double open1 = iOpen(TradeSymbol, PERIOD_M15, 1);
   double close1 = iClose(TradeSymbol, PERIOD_M15, 1);
   if(open1 <= 0.0 || close1 <= 0.0)
      return "";
   return close1 >= open1 ? "BUY" : "SELL";
}

bool BuildHighDemoTradePlan(
   const string action,
   const MqlTick &tick,
   double &entry,
   double &stop,
   double &target,
   double &risk_money,
   double &volume,
   double &requested_volume,
   bool &used_min_lot
)
{
   risk_money = 0.0;
   volume = 0.0;
   requested_volume = 0.0;
   used_min_lot = false;

   double point = SymbolInfoDouble(TradeSymbol, SYMBOL_POINT);
   if(point <= 0.0)
      return false;

   double atr = 0.0;
   if(!GetCompletedIndicatorValue(AtrHandle, atr))
   {
      if(Verbose)
         Print("MetaTraderAI HIGH DEMO plan failed: completed ATR unavailable.");
      return false;
   }

   entry = action == "BUY" ? tick.ask : tick.bid;
   double stop_points = MathMax((double)MinStopPoints, (atr * AtrMultiplier) / point);

   double swing_price = 0.0;
   if(FindRecentConfirmedSwing(
      action, PERIOD_M15, SwingLookbackBars,
      SwingLeftBars, SwingRightBars, swing_price
   ))
   {
      double buffered_swing =
         action == "BUY" ?
         swing_price - StructureBufferPoints * point :
         swing_price + StructureBufferPoints * point;
      double structure_points =
         action == "BUY" ?
         (entry - buffered_swing) / point :
         (buffered_swing - entry) / point;
      if(
         structure_points > stop_points &&
         (MaxStopPoints <= 0 || structure_points <= MaxStopPoints)
      )
         stop_points = structure_points;
   }

   long broker_stops = SymbolInfoInteger(TradeSymbol, SYMBOL_TRADE_STOPS_LEVEL);
   stop_points = MathMax(stop_points, (double)broker_stops + 5.0);
   if(MaxStopPoints > 0 && stop_points > MaxStopPoints)
   {
      if(Verbose)
         Print(
            "MetaTraderAI HIGH DEMO plan failed: stop=",
            DoubleToString(stop_points, 0),
            " points > MaxStopPoints=", MaxStopPoints
         );
      return false;
   }

   int digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   if(action == "BUY")
   {
      stop = entry - stop_points * point;
      target = entry + stop_points * RewardRiskRatio * point;
   }
   else
   {
      stop = entry + stop_points * point;
      target = entry - stop_points * RewardRiskRatio * point;
   }
   stop = NormalizeDouble(stop, digits);
   target = NormalizeDouble(target, digits);

   ENUM_ORDER_TYPE order_type = action == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double one_lot_profit = 0.0;
   if(!OrderCalcProfit(order_type, TradeSymbol, 1.0, entry, stop, one_lot_profit))
   {
      if(Verbose)
         Print("MetaTraderAI HIGH DEMO plan failed: OrderCalcProfit for 1 lot failed.");
      return false;
   }

   double one_lot_loss = MathAbs(one_lot_profit);
   if(one_lot_loss <= 0.0)
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double target_risk_money = equity * RiskModePerOrderRiskPercent() / 100.0;
   requested_volume = target_risk_money / one_lot_loss;
   volume = NormalizeVolumeDown(requested_volume);

   if(volume <= 0.0)
   {
      double minimum = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
      volume = NormalizeVolumeDown(minimum);
      used_min_lot = volume > 0.0;
   }

   if(volume <= 0.0)
   {
      Print(
         "MetaTraderAI HIGH DEMO plan failed: requested volume=",
         DoubleToString(requested_volume, 4),
         " broker min=",
         DoubleToString(SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN), 4),
         " could not produce a valid volume."
      );
      return false;
   }

   double stop_profit = 0.0;
   if(!OrderCalcProfit(order_type, TradeSymbol, volume, entry, stop, stop_profit))
      return false;
   risk_money = MathAbs(stop_profit);

   if(Verbose && used_min_lot)
   {
      Print(
         "MetaTraderAI HIGH DEMO min-lot fallback: requested=",
         DoubleToString(requested_volume, 4),
         " -> actual=", DoubleToString(volume, 4),
         " actual stop risk=$", DoubleToString(risk_money, 2)
      );
   }
   return risk_money > 0.0;
}

bool BuildProfileTradePlan(
   const string action,
   const MqlTick &tick,
   double &entry,
   double &stop,
   double &target,
   double &risk_money,
   double &volume,
   double &requested_volume,
   bool &used_min_lot
)
{
   requested_volume = 0.0;
   used_min_lot = false;

   if(RiskMode == MT_AI_HIGH)
   {
      return BuildHighDemoTradePlan(
         action, tick, entry, stop, target,
         risk_money, volume, requested_volume, used_min_lot
      );
   }

   double base_risk_money = 0.0;
   double base_volume = 0.0;
   if(!BuildTradePlan(
      action,
      tick,
      entry,
      stop,
      target,
      base_risk_money,
      base_volume
   ))
      return false;

   double base_percent = MathMin(RiskPercent, HARD_MAX_RISK_PERCENT);
   if(base_percent <= 0.0)
      return false;

   double ratio = RiskModePerOrderRiskPercent() / base_percent;
   requested_volume = base_volume * ratio;
   volume = NormalizeVolumeDown(requested_volume);
   if(volume <= 0.0)
      return false;

   ENUM_ORDER_TYPE order_type = action == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double stop_profit = 0.0;
   if(!OrderCalcProfit(order_type, TradeSymbol, volume, entry, stop, stop_profit))
      return false;

   risk_money = MathAbs(stop_profit);
   return risk_money > 0.0;
}

void MaybeProfileTrade(const string json)
{
   if(!TradingArmed) return;
   if(!IsDemoAccount()) return;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
      return;

   datetime current_bar = iTime(TradeSymbol, PERIOD_M15, 0);
   if(current_bar <= 0 || current_bar == LastExecutedM15Bar)
      return;

   string symbol = JsonValue(json, "symbol");
   string news_risk = JsonValue(json, "news_risk");
   string risk_guard = JsonValue(json, "risk_guard_status");
   string mtf_status = JsonValue(json, "mtf_status");
   int confidence = (int)StringToInteger(JsonValue(json, "confidence"));
   bool high_demo = RiskMode == MT_AI_HIGH;
   string action = high_demo ? HighDemoDirection(json) : JsonValue(json, "action");

   if(symbol != TradeSymbol) return;
   if(action != "BUY" && action != "SELL") return;
   if(confidence < RiskModeMinConfidence()) return;
   if(news_risk == "HIGH") return;
   if(!high_demo && risk_guard != "OK") return;
   if(!high_demo && mtf_status != "CONFIRM") return;

   if(high_demo && risk_guard != "OK" && Verbose)
   {
      Print(
         "MetaTraderAI HIGH DEMO: API risk_guard=", risk_guard,
         " overridden by local actual-risk ceiling ",
         DoubleToString(RiskModeDailyLossLimitPercent(), 2), "%"
      );
   }

   long spread = SymbolInfoInteger(TradeSymbol, SYMBOL_SPREAD);
   if(RiskModeMaxSpreadPoints() > 0 && spread > RiskModeMaxSpreadPoints())
   {
      if(Verbose)
         Print(
            "MetaTraderAI profile blocked: spread=", spread,
            " > limit=", RiskModeMaxSpreadPoints(), " points."
         );
      return;
   }

   int target_positions = RiskModeEffectivePositions();
   int open_positions = ManagedOpenPositions();
   if(open_positions >= target_positions)
      return;

   int positions_to_open = target_positions - open_positions;
   if(!ProfileDailyRiskAllows(positions_to_open))
      return;

   bool pullback_reentry = false;
   if(!high_demo)
   {
      if(!EntryTimingAllows(action, current_bar, pullback_reentry))
         return;
      if(!EntryCandleConfirmed(action))
      {
         if(Verbose)
            Print("MetaTraderAI profile: waiting for completed M15 entry confirmation.");
         return;
      }
   }
   else if(Verbose)
   {
      Print(
         "MetaTraderAI HIGH DEMO: aggressive basket armed. action=", action,
         " confidence=", confidence,
         " mtf=", mtf_status,
         " target_positions=", target_positions,
         " risk_target_each=", DoubleToString(RiskModePerOrderRiskPercent(), 2), "%"
      );
   }

   // Preflight one current plan so HIGH can validate the ACTUAL stop-risk of
   // the entire basket, including broker minimum-lot fallback, before order #1.
   if(high_demo)
   {
      MqlTick preflight_tick;
      if(!SymbolInfoTick(TradeSymbol, preflight_tick))
         return;

      double pre_entry = 0.0;
      double pre_stop = 0.0;
      double pre_target = 0.0;
      double pre_risk_money = 0.0;
      double pre_volume = 0.0;
      double pre_requested_volume = 0.0;
      bool pre_used_min_lot = false;
      if(!BuildProfileTradePlan(
         action, preflight_tick,
         pre_entry, pre_stop, pre_target,
         pre_risk_money, pre_volume,
         pre_requested_volume, pre_used_min_lot
      ))
      {
         Print("MetaTraderAI HIGH DEMO blocked: trade-plan preflight failed.");
         return;
      }

      if(!ActualBasketRiskAllows(
         pre_risk_money,
         positions_to_open,
         pre_requested_volume,
         pre_volume
      ))
         return;
   }

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(TradeSymbol);

   int opened = 0;
   for(int i = 0; i < positions_to_open; i++)
   {
      MqlTick tick;
      if(!SymbolInfoTick(TradeSymbol, tick))
         break;

      double entry = 0.0;
      double stop = 0.0;
      double target = 0.0;
      double risk_money = 0.0;
      double volume = 0.0;
      double requested_volume = 0.0;
      bool used_min_lot = false;
      if(!BuildProfileTradePlan(
         action, tick,
         entry, stop, target,
         risk_money, volume,
         requested_volume, used_min_lot
      ))
      {
         Print(
            "MetaTraderAI ", RiskModeName(),
            " order plan failed at basket ticket ", i + 1,
            "/", positions_to_open
         );
         break;
      }

      string comment =
         "MetaTraderAI " + RiskModeName() + " " +
         IntegerToString(i + 1) + "/" + IntegerToString(positions_to_open);

      bool request_ok = false;
      if(action == "BUY")
         request_ok = Trade.Buy(volume, TradeSymbol, 0.0, stop, target, comment);
      else
         request_ok = Trade.Sell(volume, TradeSymbol, 0.0, stop, target, comment);

      if(!request_ok || !TradeResultAccepted())
      {
         Print(
            "MetaTraderAI profile order failed. retcode=",
            Trade.ResultRetcode(), " ", Trade.ResultRetcodeDescription()
         );
         break;
      }

      opened++;
      if(Verbose)
      {
         double equity = MathMax(1.0, AccountInfoDouble(ACCOUNT_EQUITY));
         Print(
            "MetaTraderAI ", RiskModeName(), " opened ", action,
            " #", opened,
            " volume=", DoubleToString(volume, 3),
            " requested=", DoubleToString(requested_volume, 4),
            " min_lot_fallback=", used_min_lot,
            " risk=$", DoubleToString(risk_money, 2),
            " (", DoubleToString(risk_money / equity * 100.0, 2), "%)",
            " SL=", DoubleToString(stop, _Digits),
            " TP=", DoubleToString(target, _Digits)
         );
      }
   }

   if(opened > 0)
      LastExecutedM15Bar = current_bar;
}

void ProfileRefreshSignal()
{
   RefreshBridge();
   LastBridgeMs = GetTickCount64();

   string response = "";
   int status_code = 0;
   if(!FetchHint(response, status_code))
   {
      string status = "API ERROR";
      if(status_code >= 0)
         status = "HTTP " + IntegerToString(status_code);
      LastPanelStatus = status;
      DrawPanel(status, "{}");
      DrawRiskModeBadge();
      if(Verbose)
         Print("MetaTraderAI hint unavailable. HTTP=", status_code, " last_error=", GetLastError());
      return;
   }

   LastApiPayload = response;
   LastPanelStatus = "CONNECTED";
   MaybeShadow(response);
   MaybeProfileTrade(response);
   DrawPanel("CONNECTED", response);
   DrawRiskModeBadge();
}

int OnInit()
{
   if(_Symbol != TradeSymbol)
   {
      Alert("MetaTraderAI: attach this EA to ", TradeSymbol, " only.");
      return INIT_FAILED;
   }
   if(_Period != PERIOD_M15)
   {
      Alert("MetaTraderAI: attach this EA to an M15 chart.");
      return INIT_FAILED;
   }

   if(
      SnapshotBars < 21 || ContextBars < 65 || BridgeSeconds < 1 ||
      SignalSeconds < 5 || JournalSeconds < 5 || RequestTimeoutMs < 1000 ||
      PanelLeft < 0 || PanelTop < 0 || PanelWidth < 460 || PanelHeight < 400 ||
      PanelFontSize < 9 || PanelFontSize > 24 ||
      DemoDailyGoalUSD <= 0.0 || DemoGoalWindowDays < 5 || DemoGoalWindowDays > 60
   )
      return INIT_PARAMETERS_INCORRECT;

   if(
      RewardRiskRatio <= 0.0 || AtrPeriod < 2 || AtrMultiplier <= 0.0 ||
      MinStopPoints < 1 || MaxStopPoints < MinStopPoints
   )
      return INIT_PARAMETERS_INCORRECT;

   if(
      SwingLeftBars < 1 || SwingRightBars < 1 ||
      SwingLookbackBars < SwingLeftBars + SwingRightBars + 3 ||
      MaxExtensionAtr <= 0.0 || PullbackZoneAtr < 0.0 || PullbackMaxBars < 1
   )
      return INIT_PARAMETERS_INCORRECT;

   if(!SymbolSelect(TradeSymbol, true))
      return INIT_FAILED;

   AtrHandle = iATR(TradeSymbol, PERIOD_M15, AtrPeriod);
   Ema9Handle = iMA(TradeSymbol, PERIOD_M15, 9, 0, MODE_EMA, PRICE_CLOSE);
   Ema21Handle = iMA(TradeSymbol, PERIOD_M15, 21, 0, MODE_EMA, PRICE_CLOSE);
   if(AtrHandle == INVALID_HANDLE || Ema9Handle == INVALID_HANDLE || Ema21Handle == INVALID_HANDLE)
      return INIT_FAILED;

   TradingArmed = EnableAutoTrading && IsDemoAccount();
   if(EnableAutoTrading && !IsDemoAccount())
      Print("MetaTraderAI: real/contest account detected; order placement is HARD BLOCKED.");

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(TradeSymbol);

   DemoGoalStatus = "COLLECTING 0/" + IntegerToString(DemoGoalWindowDays);
   InitShadowState(ShadowA, ShadowConfidenceA);
   InitShadowState(ShadowB, ShadowConfidenceB);
   InitializeShadowJournal();

   Comment("");
   DeletePanel();
   DeleteRiskModeBadge();
   EventSetTimer(1);

   RefreshBridge();
   LastBridgeMs = GetTickCount64();
   BuildDemoJournal();
   UpdateDemoGoalStats();
   LastJournalMs = GetTickCount64();
   DrawPanel("CONNECTING", "{}");
   DrawRiskModeBadge();

   ProfileRefreshSignal();
   LastSignalMs = GetTickCount64();

   NYTrackerInit();
   NYWrapperLastObservedSignalMs = LastSignalMs;
   if(LastPanelStatus == "CONNECTED")
      NYTrackerOnSignal(LastApiPayload);
   NYTrackerOnTimer();

   Print(
      "MetaTraderAI ready: mode=", RiskModeName(),
      " demo_auto=", TradingArmed,
      " max_positions=", RiskModeEffectivePositions(),
      " target_risk_each=", DoubleToString(RiskModePerOrderRiskPercent(), 2), "%",
      " daily_ceiling=", DoubleToString(RiskModeDailyLossLimitPercent(), 2), "%"
   );
   return INIT_SUCCEEDED;
}

void OnTick()
{
   UpdateShadowPositions();
}

void OnTimer()
{
   ulong now_ms = GetTickCount64();
   UpdateShadowPositions();

   if(now_ms - LastBridgeMs >= (ulong)BridgeSeconds * 1000)
   {
      RefreshBridge();
      LastBridgeMs = now_ms;
   }

   if(now_ms - LastSignalMs >= (ulong)SignalSeconds * 1000)
   {
      ProfileRefreshSignal();
      LastSignalMs = now_ms;
   }

   if(ExportJournal && now_ms - LastJournalMs >= (ulong)JournalSeconds * 1000)
   {
      BuildDemoJournal();
      UpdateDemoGoalStats();
      LastJournalMs = now_ms;
   }

   DrawPanel(LastPanelStatus, LastApiPayload);
   DrawRiskModeBadge();

   if(LastSignalMs != NYWrapperLastObservedSignalMs)
   {
      NYWrapperLastObservedSignalMs = LastSignalMs;
      if(LastPanelStatus == "CONNECTED")
         NYTrackerOnSignal(LastApiPayload);
   }
   NYTrackerOnTimer();
}

void OnDeinit(const int reason)
{
   NYTrackerDeinit();
   DeleteRiskModeBadge();
   MetaTraderAICore_OnDeinit(reason);
}
