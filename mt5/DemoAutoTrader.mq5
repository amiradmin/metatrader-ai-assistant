#property strict
#property description "DEMO-ONLY M15 auto trader. Hard-blocks real accounts."

#include <Trade/Trade.mqh>

input string ApiUrl = "http://127.0.0.1:8000/hint";
input int RefreshSeconds = 15;
input int RequestTimeoutMs = 45000;
input int TradesPerSignal = 1;
input int MaxOpenTrades = 1;
input int MinConfidence = 75;

// Anti-chase entry timing. Direction still comes only from the M15 API signal.
input bool UseChasingFilter = true;
input double MaxExtensionAtr = 1.50;
input double PullbackZoneAtr = 0.35;
input int PullbackMaxBars = 4;

// After an overextended move, use a confirmed lower-timeframe swing for a
// precision stop if the pullback/reclaim trigger occurs.
input bool UsePullbackPrecisionStop = true;
input ENUM_TIMEFRAMES PullbackStopTimeframe = PERIOD_M5;
input int PullbackStopLookbackBars = 30;
input int PullbackStopLeftBars = 2;
input int PullbackStopRightBars = 2;
input int PullbackStopBufferPoints = 30;

// Risk model: 0.5% is also enforced as a hard ceiling in code.
input bool UseRiskBasedSizing = true;
input double RiskPercent = 0.5;
input double FallbackLotSize = 0.01;

// Dynamic protective stop for normal (non-pullback) entries.
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
int Ema9Handle = INVALID_HANDLE;
int Ema21Handle = INVALID_HANDLE;
string PendingPullbackAction = "";
datetime PendingPullbackStartedBar = 0;

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
   ENUM_ACCOUNT_TRADE_MODE mode =
      (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
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
   confidence = (int)StringToInteger(JsonValue(response, "confidence"));

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

bool GetCompletedIndicatorValue(const int handle, double &value)
{
   value = 0.0;
   if(handle == INVALID_HANDLE)
      return false;

   double values[];
   ArraySetAsSeries(values, true);
   // shift=1: entry timing and stop logic never use a forming M15 indicator bar.
   if(CopyBuffer(handle, 0, 1, 1, values) != 1)
      return false;

   value = values[0];
   return value > 0.0;
}

bool GetCompletedM15Atr(double &atr_price)
{
   return GetCompletedIndicatorValue(AtrHandle, atr_price);
}

void ResetPendingPullback(const string reason)
{
   if(PendingPullbackAction != "" && VerboseLogging && reason != "")
      Print(
         "DemoAutoTrader pullback reset: action=", PendingPullbackAction,
         " reason=", reason
      );

   PendingPullbackAction = "";
   PendingPullbackStartedBar = 0;
}

void StartPendingPullback(
   const string action,
   const datetime current_bar,
   const double extension_atr
)
{
   PendingPullbackAction = action;
   PendingPullbackStartedBar = current_bar;
   Print(
      "DemoAutoTrader anti-chase: ", action,
      " extension=", DoubleToString(extension_atr, 2),
      " ATR > limit ", DoubleToString(MaxExtensionAtr, 2),
      "; waiting for pullback/reclaim near completed M15 EMA9."
   );
}

bool PendingPullbackExpired(const datetime current_bar)
{
   if(PendingPullbackStartedBar <= 0 || current_bar <= 0)
      return true;

   int shift = iBarShift(_Symbol, PERIOD_M15, PendingPullbackStartedBar, false);
   if(shift < 0)
      return true;
   return shift > PullbackMaxBars;
}

bool EntryTimingAllows(
   const string action,
   const datetime current_bar,
   bool &pullback_reentry
)
{
   pullback_reentry = false;
   if(!UseChasingFilter)
      return true;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double atr = 0.0;
   double ema9 = 0.0;
   double ema21 = 0.0;
   if(
      !GetCompletedM15Atr(atr)
      || !GetCompletedIndicatorValue(Ema9Handle, ema9)
      || !GetCompletedIndicatorValue(Ema21Handle, ema21)
   )
   {
      Print("DemoAutoTrader skipped: M15 ATR/EMA entry context is unavailable.");
      return false;
   }

   double entry = action == "BUY" ? tick.ask : tick.bid;
   double extension_atr = action == "BUY"
      ? (entry - ema21) / atr
      : (ema21 - entry) / atr;

   if(PendingPullbackAction != "" && PendingPullbackAction != action)
      ResetPendingPullback("API direction changed");

   if(PendingPullbackAction == "")
   {
      if(extension_atr > MaxExtensionAtr)
      {
         StartPendingPullback(action, current_bar, extension_atr);
         return false;
      }
      return true;
   }

   if(PendingPullbackExpired(current_bar))
   {
      ResetPendingPullback("pullback window expired");
      if(extension_atr > MaxExtensionAtr)
         StartPendingPullback(action, current_bar, extension_atr);
      return false;
   }

   if(extension_atr > MaxExtensionAtr)
   {
      if(VerboseLogging)
         Print(
            "DemoAutoTrader pullback pending: still extended ",
            DoubleToString(extension_atr, 2), " ATR."
         );
      return false;
   }

   bool trend_aligned = action == "BUY" ? ema9 > ema21 : ema9 < ema21;
   double reclaim_distance_atr = action == "BUY"
      ? (entry - ema9) / atr
      : (ema9 - entry) / atr;
   bool reclaimed = action == "BUY"
      ? (entry >= ema9 && entry >= ema21)
      : (entry <= ema9 && entry <= ema21);
   bool in_zone = reclaim_distance_atr >= 0.0
      && reclaim_distance_atr <= PullbackZoneAtr;

   if(trend_aligned && reclaimed && in_zone)
   {
      pullback_reentry = true;
      Print(
         "DemoAutoTrader pullback READY: action=", action,
         " extension=", DoubleToString(extension_atr, 2),
         " ATR ema9_reclaim_distance=", DoubleToString(reclaim_distance_atr, 2),
         " ATR. Precision entry enabled."
      );
      ResetPendingPullback("");
      return true;
   }

   if(VerboseLogging)
      Print(
         "DemoAutoTrader pullback pending: action=", action,
         " extension=", DoubleToString(extension_atr, 2),
         " ATR ema9_distance=", DoubleToString(reclaim_distance_atr, 2),
         " zone<=", DoubleToString(PullbackZoneAtr, 2),
         " trend_aligned=", trend_aligned ? "yes" : "no",
         " reclaimed=", reclaimed ? "yes" : "no"
      );
   return false;
}

bool FindRecentConfirmedSwingAt(
   const string action,
   const ENUM_TIMEFRAMES timeframe,
   const int lookback_bars,
   const int left_bars,
   const int right_bars,
   double &swing_price
)
{
   swing_price = 0.0;
   if(lookback_bars < left_bars + right_bars + 3)
      return false;

   int first_shift = right_bars + 1;
   for(int shift = first_shift; shift <= lookback_bars; shift++)
   {
      double candidate = action == "BUY"
         ? iLow(_Symbol, timeframe, shift)
         : iHigh(_Symbol, timeframe, shift);
      if(candidate <= 0.0)
         continue;

      bool confirmed = true;
      for(int offset = 1; offset <= left_bars && confirmed; offset++)
      {
         double older = action == "BUY"
            ? iLow(_Symbol, timeframe, shift + offset)
            : iHigh(_Symbol, timeframe, shift + offset);
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

      for(int offset = 1; offset <= right_bars && confirmed; offset++)
      {
         double newer = action == "BUY"
            ? iLow(_Symbol, timeframe, shift - offset)
            : iHigh(_Symbol, timeframe, shift - offset);
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

bool PrecisionPullbackStopPoints(
   const string action,
   const double entry,
   double &stop_points
)
{
   stop_points = 0.0;
   if(!UsePullbackPrecisionStop)
      return false;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0.0)
      return false;

   double swing_price = 0.0;
   if(!FindRecentConfirmedSwingAt(
      action,
      PullbackStopTimeframe,
      PullbackStopLookbackBars,
      PullbackStopLeftBars,
      PullbackStopRightBars,
      swing_price
   ))
      return false;

   double buffered_swing = action == "BUY"
      ? swing_price - PullbackStopBufferPoints * point
      : swing_price + PullbackStopBufferPoints * point;
   double distance_points = action == "BUY"
      ? (entry - buffered_swing) / point
      : (buffered_swing - entry) / point;
   if(distance_points <= 0.0)
      return false;

   long broker_stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   distance_points = MathMax(distance_points, (double)MinStopPoints);
   distance_points = MathMax(distance_points, (double)broker_stops + 5.0);

   if(MaxStopPoints > 0 && distance_points > MaxStopPoints)
   {
      if(VerboseLogging)
         Print(
            "DemoAutoTrader precision stop rejected: ",
            DoubleToString(distance_points, 0),
            " points > MaxStopPoints ", MaxStopPoints, "."
         );
      return false;
   }

   stop_points = distance_points;
   return true;
}

bool BuildTradePlan(
   const string action,
   const MqlTick &tick,
   const bool pullback_reentry,
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

   // A chase-blocked signal may re-enter only after pullback/reclaim. For that
   // case, prefer the latest confirmed M5 (configurable) swing so the stop can
   // be materially tighter than a full M15 ATR stop while still being structural.
   if(pullback_reentry && PrecisionPullbackStopPoints(action, entry, stop_points))
   {
      stop_source = "PULLBACK_" + EnumToString(PullbackStopTimeframe) + "_SWING";
   }
   else
   {
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
      if(FindRecentConfirmedSwingAt(
         action,
         PERIOD_M15,
         SwingLookbackBars,
         SwingLeftBars,
         SwingRightBars,
         swing_price
      ))
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
                  stop_source = "ATR+M15_SWING";
               }
            }
            else if(VerboseLogging)
            {
               Print(
                  "DemoAutoTrader structure observer: recent M15 swing requires ",
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
            "DemoAutoTrader skipped: dynamic stop ",
            DoubleToString(required_points, 0),
            " points exceeds MaxStopPoints ", MaxStopPoints, "."
         );
         return false;
      }
      stop_points = required_points;
   }

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
         DoubleToString(risk_budget, 2),
         "; required raw volume=", DoubleToString(raw_volume, 4), "."
      );
      return 0.0;
   }

   planned_loss = one_lot_loss * volume;
   return volume;
}

bool OpenManagedTrade(
   const string action,
   const int trades_to_open,
   const bool pullback_reentry
)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double entry = 0.0;
   double sl = 0.0;
   double tp = 0.0;
   double stop_points = 0.0;
   string stop_source = "";
   if(!BuildTradePlan(
      action,
      tick,
      pullback_reentry,
      entry,
      sl,
      tp,
      stop_points,
      stop_source
   ))
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
         " entry_type=", pullback_reentry ? "PULLBACK" : "NORMAL",
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
      request_ok = Trade.Buy(volume, _Symbol, 0.0, sl, tp, "M15 AI PULLBACK/RISK");
   else if(action == "SELL")
      request_ok = Trade.Sell(volume, _Symbol, 0.0, sl, tp, "M15 AI PULLBACK/RISK");
   else
      return false;

   if(!request_ok || !TradeResultAccepted())
   {
      Print(
         "DemoAutoTrader order failed. retcode=", Trade.ResultRetcode(),
         " ", Trade.ResultRetcodeDescription()
      );
      return false;
   }

   if(VerboseLogging)
      Print(
         "DemoAutoTrader order accepted: action=", action,
         " volume=", DoubleToString(volume, 3),
         " deal=", Trade.ResultDeal(),
         " order=", Trade.ResultOrder(),
         " retcode=", Trade.ResultRetcode(),
         " ", Trade.ResultRetcodeDescription()
      );
   return true;
}

void EvaluateAndTrade()
{
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

   if(symbol != _Symbol)
   {
      ResetPendingPullback("symbol mismatch");
      Print("DemoAutoTrader skipped: API symbol ", symbol, " != chart symbol ", _Symbol);
      return;
   }

   if(action != "BUY" && action != "SELL")
   {
      ResetPendingPullback("API action is not directional");
      if(VerboseLogging)
         Print("DemoAutoTrader skipped: API action is ", action, ".");
      return;
   }

   if(confidence < MinConfidence)
   {
      ResetPendingPullback("confidence fell below threshold");
      Print("DemoAutoTrader skipped: confidence ", confidence, " < ", MinConfidence);
      return;
   }

   if(news_risk == "HIGH")
   {
      ResetPendingPullback("HIGH news risk");
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

   bool pullback_reentry = false;
   if(!EntryTimingAllows(action, current_bar, pullback_reentry))
      return;

   int requested = MathMax(1, TradesPerSignal);
   int to_open = MathMin(requested, capacity);
   int opened = 0;

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(_Symbol);

   for(int i = 0; i < to_open; i++)
   {
      if(OpenManagedTrade(action, to_open, pullback_reentry))
      {
         opened++;
         Print(
            "DemoAutoTrader opened ", action,
            " #", opened,
            " confidence=", confidence,
            " entry_type=", pullback_reentry ? "PULLBACK" : "NORMAL"
         );
      }
      else
      {
         Print("DemoAutoTrader trade plan/order not opened; see prior log line(s).");
         break;
      }
   }

   if(opened > 0)
   {
      LastExecutedM15Bar = current_bar;
      ResetPendingPullback("");
   }
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
   if(MaxExtensionAtr <= 0.0 || PullbackZoneAtr < 0.0 || PullbackMaxBars < 1)
      return INIT_PARAMETERS_INCORRECT;
   if(
      PullbackStopLookbackBars < PullbackStopLeftBars + PullbackStopRightBars + 3
      || PullbackStopLeftBars < 1
      || PullbackStopRightBars < 1
      || PullbackStopBufferPoints < 0
   )
      return INIT_PARAMETERS_INCORRECT;

   if(!IsDemoAccount())
   {
      Alert("DemoAutoTrader is DEMO-ONLY and is blocked on this account.");
      return INIT_FAILED;
   }

   if(UseDynamicStop || UseChasingFilter)
   {
      AtrHandle = iATR(_Symbol, PERIOD_M15, AtrPeriod);
      if(AtrHandle == INVALID_HANDLE)
      {
         Print("DemoAutoTrader init failed: could not create M15 ATR handle.");
         return INIT_FAILED;
      }
   }

   if(UseChasingFilter)
   {
      Ema9Handle = iMA(_Symbol, PERIOD_M15, 9, 0, MODE_EMA, PRICE_CLOSE);
      Ema21Handle = iMA(_Symbol, PERIOD_M15, 21, 0, MODE_EMA, PRICE_CLOSE);
      if(Ema9Handle == INVALID_HANDLE || Ema21Handle == INVALID_HANDLE)
      {
         Print("DemoAutoTrader init failed: could not create M15 EMA handles.");
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
      " M15-first; chart_tf=", EnumToString((ENUM_TIMEFRAMES)_Period),
      " MinConfidence=", MinConfidence,
      " risk=", DoubleToString(MathMin(RiskPercent, HARD_MAX_RISK_PERCENT), 2), "%",
      " anti_chase=", UseChasingFilter ? "ON" : "OFF",
      " max_extension=", DoubleToString(MaxExtensionAtr, 2), " ATR",
      " pullback_zone=", DoubleToString(PullbackZoneAtr, 2), " ATR",
      " dynamic_stop=", UseDynamicStop ? "ON" : "OFF"
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
   ResetPendingPullback("");

   if(AtrHandle != INVALID_HANDLE)
   {
      IndicatorRelease(AtrHandle);
      AtrHandle = INVALID_HANDLE;
   }
   if(Ema9Handle != INVALID_HANDLE)
   {
      IndicatorRelease(Ema9Handle);
      Ema9Handle = INVALID_HANDLE;
   }
   if(Ema21Handle != INVALID_HANDLE)
   {
      IndicatorRelease(Ema21Handle);
      Ema21Handle = INVALID_HANDLE;
   }
}
