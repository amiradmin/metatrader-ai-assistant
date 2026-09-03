#property strict
#property description "DEMO-ONLY M15 auto trader. Hard-blocks real accounts."

#include <Trade/Trade.mqh>

input string ApiUrl = "http://127.0.0.1:8000/hint";
input int RefreshSeconds = 15;
input int RequestTimeoutMs = 45000;
input int TradesPerSignal = 1;
input int MaxOpenTrades = 3;
input int MinConfidence = 75;

// Risk model: 0.5% is also enforced as a hard ceiling in code.
input bool UseRiskBasedSizing = true;
input double RiskPercent = 0.5;
input double FallbackLotSize = 0.01;

// Dynamic protective stop: completed M15 ATR plus the latest confirmed M15 swing.
input bool UseDynamicStop = true;
input int AtrPeriod = 14;
input double AtrMultiplier = 1.50;
input int SwingLookbackBars = 30;
input int SwingLeftBars = 2;
input int SwingRightBars = 2;
input int StructureBufferPoints = 50;
input int MinStopPoints = 150;
input int MaxStopPoints = 1200;
input double RewardRiskRatio = 2.0;

// Used only when UseDynamicStop=false.
input int FallbackStopLossPoints = 300;
input int FallbackTakeProfitPoints = 600;

input int MaxSpreadPoints = 50;
input ulong MagicNumber = 26090315;
input int SlippagePoints = 20;
input bool VerboseLogging = true;

const double HARD_MAX_RISK_PERCENT = 0.5;

CTrade Trade;
datetime LastExecutedM15Bar = 0;
int AtrHandle = INVALID_HANDLE;

string JsonValue(const string json, const string key)
{
   string needle = "\"" + key + "\"";
   int position = StringFind(json, needle);
   if(position < 0)
      return "";

   position = StringFind(json, ":", position + StringLen(needle));
   if(position < 0)
      return "";
   position++;

   int length = StringLen(json);
   while(position < length)
   {
      ushort character = StringGetCharacter(json, position);
      if(character != ' ' && character != '\t')
         break;
      position++;
   }

   if(position < length && StringGetCharacter(json, position) == '"')
   {
      int ending = StringFind(json, "\"", position + 1);
      if(ending < 0)
         return "";
      return StringSubstr(json, position + 1, ending - position - 1);
   }

   int comma = StringFind(json, ",", position);
   int brace = StringFind(json, "}", position);
   int ending = comma;
   if(ending < 0 || (brace >= 0 && brace < ending))
      ending = brace;
   if(ending < 0)
      ending = length;
   return StringSubstr(json, position, ending - position);
}

bool IsDemoAccount()
{
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   return mode == ACCOUNT_TRADE_MODE_DEMO;
}

int VolumeDigits(const double step)
{
   if(step >= 1.0)
      return 0;
   if(step >= 0.1)
      return 1;
   if(step >= 0.01)
      return 2;
   if(step >= 0.001)
      return 3;
   return 4;
}

double NormalizeVolumeNearest(const double requested)
{
   double minimum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double value = MathMax(minimum, MathMin(maximum, requested));
   if(step > 0.0)
      value = minimum + MathFloor((value - minimum) / step + 0.5) * step;

   return NormalizeDouble(value, VolumeDigits(step));
}

double NormalizeVolumeDown(const double requested)
{
   // Never round risk-based size upward: that could exceed the risk budget.
   double minimum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(requested + 1e-12 < minimum)
      return 0.0;

   double value = MathMin(maximum, requested);
   if(step > 0.0)
      value = minimum + MathFloor((value - minimum + 1e-12) / step) * step;

   if(value + 1e-12 < minimum)
      return 0.0;
   return NormalizeDouble(value, VolumeDigits(step));
}

int ManagedOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(symbol == _Symbol && (ulong)magic == MagicNumber)
         count++;
   }
   return count;
}

long CurrentSpreadPoints()
{
   return SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
}

bool SpreadIsAcceptable()
{
   if(MaxSpreadPoints <= 0)
      return true;
   return CurrentSpreadPoints() <= MaxSpreadPoints;
}

bool FetchHint(string &action, int &confidence, string &symbol, string &news_risk)
{
   char request_data[];
   char response_data[];
   string response_headers;
   ArrayResize(request_data, 0);

   ResetLastError();
   int status_code = WebRequest(
      "GET",
      ApiUrl,
      "",
      RequestTimeoutMs,
      request_data,
      response_data,
      response_headers
   );

   if(status_code == -1)
   {
      Print("DemoAutoTrader WebRequest failed: ", GetLastError());
      return false;
   }

   string response = CharArrayToString(response_data, 0, -1, CP_UTF8);
   if(status_code != 200)
   {
      Print("DemoAutoTrader API HTTP ", status_code, ": ", response);
      return false;
   }

   action = JsonValue(response, "action");
   symbol = JsonValue(response, "symbol");
   news_risk = JsonValue(response, "news_risk");
   string confidence_text = JsonValue(response, "confidence");
   confidence = (int)StringToInteger(confidence_text);

   return action != "" && symbol != "";
}

bool TradeResultAccepted()
{
   uint retcode = Trade.ResultRetcode();
   return (
      retcode == TRADE_RETCODE_DONE
      || retcode == TRADE_RETCODE_DONE_PARTIAL
      || retcode == TRADE_RETCODE_PLACED
   );
}

bool GetCompletedM15Atr(double &atr_price)
{
   atr_price = 0.0;
   if(AtrHandle == INVALID_HANDLE)
      return false;

   double values[];
   ArraySetAsSeries(values, true);
   // shift=1 means the still-forming M15 candle cannot change the stop plan.
   if(CopyBuffer(AtrHandle, 0, 1, 1, values) != 1)
      return false;

   atr_price = values[0];
   return atr_price > 0.0;
}

bool FindRecentConfirmedSwing(const string action, double &swing_price)
{
   swing_price = 0.0;
   if(SwingLookbackBars < SwingLeftBars + SwingRightBars + 3)
      return false;

   int first_shift = SwingRightBars + 1;
   int last_shift = SwingLookbackBars;

   for(int shift = first_shift; shift <= last_shift; shift++)
   {
      double candidate = (action == "BUY")
         ? iLow(_Symbol, PERIOD_M15, shift)
         : iHigh(_Symbol, PERIOD_M15, shift);
      if(candidate <= 0.0)
         continue;

      bool confirmed = true;
      for(int offset = 1; offset <= SwingLeftBars && confirmed; offset++)
      {
         double older = (action == "BUY")
            ? iLow(_Symbol, PERIOD_M15, shift + offset)
            : iHigh(_Symbol, PERIOD_M15, shift + offset);
         if(older <= 0.0)
         {
            confirmed = false;
            break;
         }
         if(action == "BUY" && candidate >= older)
            confirmed = false;
         if(action == "SELL" && candidate <= older)
            confirmed = false;
      }

      for(int offset = 1; offset <= SwingRightBars && confirmed; offset++)
      {
         double newer = (action == "BUY")
            ? iLow(_Symbol, PERIOD_M15, shift - offset)
            : iHigh(_Symbol, PERIOD_M15, shift - offset);
         if(newer <= 0.0)
         {
            confirmed = false;
            break;
         }
         if(action == "BUY" && candidate > newer)
            confirmed = false;
         if(action == "SELL" && candidate < newer)
            confirmed = false;
      }

      if(confirmed)
      {
         swing_price = candidate;
         return true;
      }
   }
   return false;
}

bool BuildTradePlan(
   const string action,
   const MqlTick &tick,
   double &entry,
   double &stop,
   double &target,
   double &stop_points,
   string &stop_source
)
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   if(point <= 0.0)
      return false;

   entry = action == "BUY" ? tick.ask : tick.bid;
   stop = 0.0;
   target = 0.0;
   stop_points = 0.0;
   stop_source = "FIXED";

   if(!UseDynamicStop)
   {
      if(FallbackStopLossPoints <= 0 || FallbackTakeProfitPoints <= 0)
         return false;
      stop_points = FallbackStopLossPoints;
      if(action == "BUY")
      {
         stop = entry - stop_points * point;
         target = entry + FallbackTakeProfitPoints * point;
      }
      else
      {
         stop = entry + stop_points * point;
         target = entry - FallbackTakeProfitPoints * point;
      }
      stop = NormalizeDouble(stop, digits);
      target = NormalizeDouble(target, digits);
      return true;
   }

   double atr_price = 0.0;
   if(!GetCompletedM15Atr(atr_price))
   {
      Print("DemoAutoTrader skipped: completed M15 ATR is unavailable.");
      return false;
   }

   double atr_points = (atr_price * AtrMultiplier) / point;
   double required_points = MathMax((double)MinStopPoints, atr_points);
   stop_source = "ATR";

   double swing_price = 0.0;
   if(FindRecentConfirmedSwing(action, swing_price))
   {
      double buffered_swing = action == "BUY"
         ? swing_price - StructureBufferPoints * point
         : swing_price + StructureBufferPoints * point;
      double structure_points = action == "BUY"
         ? (entry - buffered_swing) / point
         : (buffered_swing - entry) / point;

      if(structure_points > 0.0)
      {
         if(MaxStopPoints <= 0 || structure_points <= MaxStopPoints)
         {
            if(structure_points > required_points)
            {
               required_points = structure_points;
               stop_source = "ATR+SWING";
            }
         }
         else if(VerboseLogging)
         {
            Print(
               "DemoAutoTrader structure observer: recent swing requires ",
               DoubleToString(structure_points, 0),
               " points > MaxStopPoints ", MaxStopPoints,
               "; ATR stop retained."
            );
         }
      }
   }

   long broker_stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   required_points = MathMax(required_points, (double)broker_stops + 5.0);

   if(MaxStopPoints > 0 && required_points > MaxStopPoints)
   {
      Print(
         "DemoAutoTrader skipped: dynamic stop ", DoubleToString(required_points, 0),
         " points exceeds MaxStopPoints ", MaxStopPoints, "."
      );
      return false;
   }

   stop_points = required_points;
   double target_points = stop_points * RewardRiskRatio;
   if(action == "BUY")
   {
      stop = entry - stop_points * point;
      target = entry + target_points * point;
   }
   else
   {
      stop = entry + stop_points * point;
      target = entry - target_points * point;
   }

   stop = NormalizeDouble(stop, digits);
   target = NormalizeDouble(target, digits);
   return true;
}

double RiskSizedVolume(
   const string action,
   const double entry,
   const double stop,
   const int trades_to_open,
   double &risk_budget,
   double &planned_loss
)
{
   risk_budget = 0.0;
   planned_loss = 0.0;

   if(!UseRiskBasedSizing)
      return NormalizeVolumeNearest(FallbackLotSize);

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double effective_risk_percent = MathMin(RiskPercent, HARD_MAX_RISK_PERCENT);
   risk_budget = equity * effective_risk_percent / 100.0;
   risk_budget /= MathMax(1, trades_to_open);

   ENUM_ORDER_TYPE order_type = action == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double one_lot_profit = 0.0;
   if(!OrderCalcProfit(order_type, _Symbol, 1.0, entry, stop, one_lot_profit))
   {
      Print("DemoAutoTrader skipped: OrderCalcProfit failed for risk sizing.");
      return 0.0;
   }

   double one_lot_loss = MathAbs(one_lot_profit);
   if(one_lot_loss <= 0.0)
      return 0.0;

   double raw_volume = risk_budget / one_lot_loss;
   double volume = NormalizeVolumeDown(raw_volume);
   if(volume <= 0.0)
   {
      Print(
         "DemoAutoTrader skipped: broker minimum lot would exceed risk budget $",
         DoubleToString(risk_budget, 2), "."
      );
      return 0.0;
   }

   planned_loss = one_lot_loss * volume;
   return volume;
}

bool OpenManagedTrade(const string action, const int trades_to_open)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double entry = 0.0;
   double sl = 0.0;
   double tp = 0.0;
   double stop_points = 0.0;
   string stop_source = "";
   if(!BuildTradePlan(action, tick, entry, sl, tp, stop_points, stop_source))
      return false;

   double risk_budget = 0.0;
   double planned_loss = 0.0;
   double volume = RiskSizedVolume(
      action,
      entry,
      sl,
      trades_to_open,
      risk_budget,
      planned_loss
   );
   if(volume <= 0.0)
      return false;

   if(VerboseLogging)
   {
      Print(
         "DemoAutoTrader plan: action=", action,
         " stop_source=", stop_source,
         " stop_points=", DoubleToString(stop_points, 0),
         " RR=", DoubleToString(RewardRiskRatio, 2),
         " volume=", DoubleToString(volume, 3),
         " risk_budget=$", DoubleToString(risk_budget, 2),
         " planned_loss~$", DoubleToString(planned_loss, 2),
         " SL=", DoubleToString(sl, _Digits),
         " TP=", DoubleToString(tp, _Digits)
      );
   }

   bool request_ok = false;
   if(action == "BUY")
      request_ok = Trade.Buy(volume, _Symbol, 0.0, sl, tp, "M15 AI DEMO RISK");
   else if(action == "SELL")
      request_ok = Trade.Sell(volume, _Symbol, 0.0, sl, tp, "M15 AI DEMO RISK");
   else
      return false;

   if(!request_ok || !TradeResultAccepted())
      return false;

   if(VerboseLogging)
   {
      Print(
         "DemoAutoTrader order accepted: action=", action,
         " volume=", DoubleToString(volume, 3),
         " deal=", Trade.ResultDeal(),
         " order=", Trade.ResultOrder(),
         " retcode=", Trade.ResultRetcode(),
         " ", Trade.ResultRetcodeDescription()
      );
   }
   return true;
}

void EvaluateAndTrade()
{
   // Hard safety lock: never trade a real/contest account.
   if(!IsDemoAccount())
   {
      Print("DemoAutoTrader BLOCKED: account is not DEMO.");
      return;
   }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      if(VerboseLogging)
         Print("DemoAutoTrader waiting: Algo Trading / EA trading is not allowed.");
      return;
   }

   datetime current_bar = iTime(_Symbol, PERIOD_M15, 0);
   if(current_bar <= 0 || current_bar == LastExecutedM15Bar)
      return;

   string action;
   string symbol;
   string news_risk;
   int confidence = 0;
   if(!FetchHint(action, confidence, symbol, news_risk))
      return;

   long spread_points = CurrentSpreadPoints();
   if(VerboseLogging)
   {
      Print(
         "DemoAutoTrader signal: action=", action,
         " confidence=", confidence,
         " min=", MinConfidence,
         " news=", news_risk,
         " spread=", spread_points,
         " max_spread=", MaxSpreadPoints,
         " open=", ManagedOpenPositions(),
         "/", MaxOpenTrades
      );
   }

   if(symbol != _Symbol)
   {
      Print("DemoAutoTrader skipped: API symbol ", symbol, " != chart symbol ", _Symbol);
      return;
   }

   // Confidence alone never creates a direction: the API must explicitly return BUY/SELL.
   if(action != "BUY" && action != "SELL")
   {
      if(VerboseLogging)
         Print("DemoAutoTrader skipped: API action is ", action, ".");
      return;
   }

   // Exact threshold is allowed: confidence == MinConfidence passes.
   if(confidence < MinConfidence)
   {
      Print("DemoAutoTrader skipped: confidence ", confidence, " < ", MinConfidence);
      return;
   }

   if(news_risk == "HIGH")
   {
      Print("DemoAutoTrader skipped: HIGH news risk.");
      return;
   }

   if(!SpreadIsAcceptable())
   {
      Print(
         "DemoAutoTrader skipped: spread ", spread_points,
         " > MaxSpreadPoints ", MaxSpreadPoints, "."
      );
      return;
   }

   int already_open = ManagedOpenPositions();
   int capacity = MaxOpenTrades - already_open;
   if(capacity <= 0)
   {
      if(VerboseLogging)
         Print("DemoAutoTrader skipped: MaxOpenTrades reached.");
      return;
   }

   int requested = MathMax(1, TradesPerSignal);
   int to_open = MathMin(requested, capacity);
   int opened = 0;

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(_Symbol);

   for(int i = 0; i < to_open; i++)
   {
      if(OpenManagedTrade(action, to_open))
      {
         opened++;
         Print("DemoAutoTrader opened ", action, " #", opened, " confidence=", confidence);
      }
      else
      {
         Print(
            "DemoAutoTrader order not opened. retcode=",
            Trade.ResultRetcode(),
            " ",
            Trade.ResultRetcodeDescription()
         );
         break;
      }
   }

   // Mark the bar only after the broker reports an accepted trade result.
   if(opened > 0)
      LastExecutedM15Bar = current_bar;
}

int OnInit()
{
   if(RefreshSeconds < 5 || RequestTimeoutMs < 1000)
      return INIT_PARAMETERS_INCORRECT;
   if(TradesPerSignal < 1 || MaxOpenTrades < 1 || FallbackLotSize <= 0.0)
      return INIT_PARAMETERS_INCORRECT;
   if(MinConfidence < 0 || MinConfidence > 100)
      return INIT_PARAMETERS_INCORRECT;
   if(RiskPercent <= 0.0 || RiskPercent > HARD_MAX_RISK_PERCENT)
      return INIT_PARAMETERS_INCORRECT;
   if(RewardRiskRatio <= 0.0 || AtrPeriod < 2 || AtrMultiplier <= 0.0)
      return INIT_PARAMETERS_INCORRECT;
   if(MinStopPoints < 1 || MaxStopPoints < MinStopPoints)
      return INIT_PARAMETERS_INCORRECT;
   if(SwingLeftBars < 1 || SwingRightBars < 1)
      return INIT_PARAMETERS_INCORRECT;

   if(!IsDemoAccount())
   {
      Alert("DemoAutoTrader is DEMO-ONLY and is blocked on this account.");
      return INIT_FAILED;
   }

   if(UseDynamicStop)
   {
      AtrHandle = iATR(_Symbol, PERIOD_M15, AtrPeriod);
      if(AtrHandle == INVALID_HANDLE)
      {
         Print("DemoAutoTrader init failed: could not create M15 ATR handle.");
         return INIT_FAILED;
      }
   }

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(_Symbol);
   EventSetTimer(RefreshSeconds);
   Print(
      "DemoAutoTrader ready on DEMO account. Symbol=", _Symbol,
      " M15-first. MinConfidence=", MinConfidence,
      " risk=", DoubleToString(MathMin(RiskPercent, HARD_MAX_RISK_PERCENT), 2), "%",
      " dynamic_stop=", UseDynamicStop ? "ON" : "OFF",
      ". Exact threshold is eligible when API action is BUY/SELL."
   );
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   EvaluateAndTrade();
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   if(AtrHandle != INVALID_HANDLE)
   {
      IndicatorRelease(AtrHandle);
      AtrHandle = INVALID_HANDLE;
   }
}
