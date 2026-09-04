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

// HIGH is intentionally wild for DEMO stress testing:
// - confidence floor 50
// - 5-ticket basket on hedging accounts
// - 0.50% planned risk per ticket (about 2.5% for a full basket)
// - if the strict API action is WAIT, direction is derived from technical_score
// - MTF / anti-chase / pullback / rejection confirmation are bypassed in HIGH
// - demo-only, risk guard, high-impact-news and spread gates remain hard stops
input ENUM_MT_AI_RISK_MODE RiskMode = MT_AI_LOW;
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
   if(RiskMode == MT_AI_HIGH) return 50;
   if(RiskMode == MT_AI_MEDIUM) return 78;
   return 82;
}

double RiskModePerOrderRiskPercent()
{
   if(RiskMode == MT_AI_HIGH) return 0.50;
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
   if(RiskMode == MT_AI_HIGH) return 15.0;
   if(RiskMode == MT_AI_MEDIUM) return 2.5;
   return 1.5;
}

int RiskModeMaxSpreadPoints()
{
   if(RiskMode == MT_AI_HIGH) return 100;
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
      " | " + DoubleToString(RiskModePerOrderRiskPercent(), 2) + "% each"
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
            "MetaTraderAI profile gate: projected risk ",
            DoubleToString(projected, 2),
            "% > ", DoubleToString(RiskModeDailyLossLimitPercent(), 2), "%"
         );
      }
      return false;
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

   // Exact zero is resolved by the latest completed M15 candle so HIGH mode
   // still has a deterministic demo direction instead of a random coin flip.
   double open1 = iOpen(TradeSymbol, PERIOD_M15, 1);
   double close1 = iClose(TradeSymbol, PERIOD_M15, 1);
   if(open1 <= 0.0 || close1 <= 0.0)
      return "";
   return close1 >= open1 ? "BUY" : "SELL";
}

bool BuildProfileTradePlan(
   const string action,
   const MqlTick &tick,
   double &entry,
   double &stop,
   double &target,
   double &risk_money,
   double &volume
)
{
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
   volume = NormalizeVolumeDown(base_volume * ratio);
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
   if(risk_guard != "OK") return;
   if(!high_demo && mtf_status != "CONFIRM") return;

   long spread = SymbolInfoInteger(TradeSymbol, SYMBOL_SPREAD);
   if(RiskModeMaxSpreadPoints() > 0 && spread > RiskModeMaxSpreadPoints())
      return;

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
         "MetaTraderAI HIGH DEMO: immediate basket armed. action=", action,
         " confidence=", confidence,
         " mtf=", mtf_status,
         " (MTF/pullback/rejection bypassed in HIGH DEMO)"
      );
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
      if(!BuildProfileTradePlan(action, tick, entry, stop, target, risk_money, volume))
         break;

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
         Print(
            "MetaTraderAI ", RiskModeName(), " opened ", action,
            " #", opened,
            " volume=", DoubleToString(volume, 3),
            " risk=$", DoubleToString(risk_money, 2),
            " SL=", DoubleToString(stop, _Digits),
            " TP=", DoubleToString(target, _Digits),
            " pullback=", pullback_reentry
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

   // ProfileRefreshSignal is used immediately so HIGH DEMO can open its basket
   // as soon as the EA is attached/reloaded and the current hint is acceptable.
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
      " risk_each=", DoubleToString(RiskModePerOrderRiskPercent(), 2), "%"
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
