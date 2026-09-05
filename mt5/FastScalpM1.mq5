#property strict
#property description "FAST_SCALP_M1: M1 bridge + guarded DEMO-only execution"

#include <Trade/Trade.mqh>

input string TradeSymbol = "XAUUSD_o";
input string ApiUrl = "http://127.0.0.1:8000/fast-scalp/hint";
input string SnapshotFile = "fast_scalp_m1_snapshot.json";
input int SnapshotBars = 120;
input int M5Bars = 60;
input int BridgeSeconds = 1;
input int SignalSeconds = 2;
input int RequestTimeoutMs = 3500;

input bool EnableAutoTrading = true;
input int MinConfidence = 72;
input double RiskPercent = 0.25;
input double MaxDailyLossPercent = 1.0;
input int MaxOpenTrades = 2;
input double RewardRiskRatio = 1.50;
input int MaxSpreadPoints = 35;
input int SlippagePoints = 15;
input int CooldownSeconds = 30;
input ulong MagicNumber = 26090501;

input int AtrPeriod = 14;
input double AtrMultiplier = 1.0;
input int MinStopPoints = 80;
input int MaxStopPoints = 500;
input bool Verbose = true;

const double HARD_MAX_RISK_PERCENT = 0.50;
CTrade Trade;
int AtrHandle = INVALID_HANDLE;
ulong LastBridgeMs = 0;
ulong LastSignalMs = 0;
datetime LastExecutedM1Bar = 0;
datetime LastEntryTime = 0;
string LastPayload = "{}";
string LastStatus = "STARTING";

bool IsDemoAccount()
{
   ENUM_ACCOUNT_TRADE_MODE mode =
      (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   return mode == ACCOUNT_TRADE_MODE_DEMO;
}

bool IsHedgingAccount()
{
   ENUM_ACCOUNT_MARGIN_MODE mode =
      (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   return mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
}

int EffectiveMaxOpenTrades()
{
   if(MaxOpenTrades <= 1)
      return 1;
   return IsHedgingAccount() ? MaxOpenTrades : 1;
}

string EscapeJson(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   return value;
}

string UtcIsoTimestamp()
{
   MqlDateTime value;
   TimeToStruct(TimeGMT(), value);
   return StringFormat(
      "%04d-%02d-%02dT%02d:%02d:%02dZ",
      value.year, value.mon, value.day,
      value.hour, value.min, value.sec
   );
}

datetime DayStartOf(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   parts.hour = 0;
   parts.min = 0;
   parts.sec = 0;
   return StructToTime(parts);
}

bool AccountDayRiskMetrics(double &realized_pnl, double &day_start_balance)
{
   realized_pnl = 0.0;
   day_start_balance = 0.0;
   if(!HistorySelect(DayStartOf(TimeCurrent()), TimeCurrent()))
      return false;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;
      ENUM_DEAL_TYPE type =
         (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue;

      realized_pnl += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      realized_pnl += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      realized_pnl += HistoryDealGetDouble(ticket, DEAL_SWAP);
      realized_pnl += HistoryDealGetDouble(ticket, DEAL_FEE);
   }

   day_start_balance = AccountInfoDouble(ACCOUNT_BALANCE) - realized_pnl;
   return day_start_balance > 0.0;
}

bool LocalDailyRiskAllows()
{
   double realized_pnl = 0.0;
   double day_start_balance = 0.0;
   if(!AccountDayRiskMetrics(realized_pnl, day_start_balance))
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double drawdown = MathMax(
      0.0,
      (day_start_balance - equity) / day_start_balance * 100.0
   );
   double projected = drawdown + MathMin(RiskPercent, HARD_MAX_RISK_PERCENT);
   return drawdown < MaxDailyLossPercent && projected <= MaxDailyLossPercent;
}

bool WriteTextFile(const string file_name, const string payload)
{
   int handle = FileOpen(file_name, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      if(Verbose)
         Print("FAST_SCALP_M1: FileOpen failed: ", GetLastError());
      return false;
   }
   FileWriteString(handle, payload);
   FileClose(handle);
   return true;
}

bool CopyCompletedRates(
   const ENUM_TIMEFRAMES timeframe,
   const int requested_bars,
   MqlRates &rates[],
   int &copied,
   const int minimum_bars
)
{
   ArraySetAsSeries(rates, true);
   copied = CopyRates(TradeSymbol, timeframe, 1, requested_bars, rates);
   return copied >= minimum_bars;
}

string RatesField(
   MqlRates &rates[],
   const int copied,
   const string field,
   const int digits
)
{
   string json = "\"" + field + "\":[";
   for(int i = copied - 1; i >= 0; i--)
   {
      double value = rates[i].close;
      if(field == "opens") value = rates[i].open;
      else if(field == "highs") value = rates[i].high;
      else if(field == "lows") value = rates[i].low;

      json += DoubleToString(value, digits);
      if(i > 0) json += ",";
   }
   json += "]";
   return json;
}

string TickVolumeField(MqlRates &rates[], const int copied)
{
   string json = "\"tick_volumes\":[";
   for(int i = copied - 1; i >= 0; i--)
   {
      json += IntegerToString((int)rates[i].tick_volume);
      if(i > 0) json += ",";
   }
   json += "]";
   return json;
}

string M5ClosesField(MqlRates &rates[], const int copied, const int digits)
{
   string json = "\"m5_closes\":[";
   for(int i = copied - 1; i >= 0; i--)
   {
      json += DoubleToString(rates[i].close, digits);
      if(i > 0) json += ",";
   }
   json += "]";
   return json;
}

bool WriteSnapshot()
{
   MqlTick tick;
   if(!SymbolInfoTick(TradeSymbol, tick))
      return false;

   int digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   MqlRates m1[];
   MqlRates m5[];
   int copied_m1 = 0;
   int copied_m5 = 0;
   if(!CopyCompletedRates(PERIOD_M1, SnapshotBars, m1, copied_m1, 30))
      return false;
   if(!CopyCompletedRates(PERIOD_M5, M5Bars, m5, copied_m5, 20))
      return false;

   double day_realized_pnl = 0.0;
   double day_start_balance = 0.0;
   bool has_day_metrics =
      AccountDayRiskMetrics(day_realized_pnl, day_start_balance);

   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(TradeSymbol) + "\",";
   json += "\"timeframe\":\"PERIOD_M1\",";
   json += "\"generated_at\":\"" + UtcIsoTimestamp() + "\",";
   json += "\"bid\":" + DoubleToString(tick.bid, digits) + ",";
   json += "\"ask\":" + DoubleToString(tick.ask, digits) + ",";
   json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   json += "\"positions_total\":" + IntegerToString(PositionsTotal()) + ",";
   if(has_day_metrics)
   {
      json += "\"day_start_balance\":" + DoubleToString(day_start_balance, 2) + ",";
      json += "\"day_realized_pnl\":" + DoubleToString(day_realized_pnl, 2) + ",";
   }
   json += RatesField(m1, copied_m1, "opens", digits) + ",";
   json += RatesField(m1, copied_m1, "highs", digits) + ",";
   json += RatesField(m1, copied_m1, "lows", digits) + ",";
   json += RatesField(m1, copied_m1, "closes", digits) + ",";
   json += TickVolumeField(m1, copied_m1) + ",";
   json += M5ClosesField(m5, copied_m5, digits);
   json += "}";
   return WriteTextFile(SnapshotFile, json);
}

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
      if(character != 32 && character != 9)
         break;
      position++;
   }

   if(position < length && StringGetCharacter(json, position) == 34)
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

bool FetchHint(string &response, int &status_code)
{
   char request_data[];
   char response_data[];
   string response_headers;
   ArrayResize(request_data, 0);
   ResetLastError();

   status_code = WebRequest(
      "GET", ApiUrl, "", RequestTimeoutMs,
      request_data, response_data, response_headers
   );
   if(status_code == -1)
   {
      response = "";
      return false;
   }

   response = CharArrayToString(response_data, 0, -1, CP_UTF8);
   return status_code == 200;
}

int VolumeDigits(const double step)
{
   if(step >= 1.0) return 0;
   if(step >= 0.1) return 1;
   if(step >= 0.01) return 2;
   if(step >= 0.001) return 3;
   return 4;
}

double NormalizeVolumeDown(const double requested)
{
   double minimum = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(TradeSymbol, SYMBOL_VOLUME_STEP);
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
      if(
         PositionGetString(POSITION_SYMBOL) == TradeSymbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == MagicNumber
      )
         count++;
   }
   return count;
}

bool GetCompletedAtr(double &atr)
{
   atr = 0.0;
   if(AtrHandle == INVALID_HANDLE)
      return false;
   double values[];
   ArraySetAsSeries(values, true);
   if(CopyBuffer(AtrHandle, 0, 1, 1, values) != 1)
      return false;
   atr = values[0];
   return atr > 0.0;
}

bool BuildTradePlan(
   const string action,
   const MqlTick &tick,
   double &stop,
   double &target,
   double &risk_money,
   double &volume
)
{
   double point = SymbolInfoDouble(TradeSymbol, SYMBOL_POINT);
   if(point <= 0.0)
      return false;

   double atr = 0.0;
   if(!GetCompletedAtr(atr))
      return false;

   double entry = action == "BUY" ? tick.ask : tick.bid;
   double stop_points = MathMax(
      (double)MinStopPoints,
      (atr * AtrMultiplier) / point
   );
   long broker_stops = SymbolInfoInteger(TradeSymbol, SYMBOL_TRADE_STOPS_LEVEL);
   stop_points = MathMax(stop_points, (double)broker_stops + 5.0);
   if(MaxStopPoints > 0 && stop_points > MaxStopPoints)
      return false;

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

   ENUM_ORDER_TYPE order_type =
      action == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double one_lot_profit = 0.0;
   if(!OrderCalcProfit(order_type, TradeSymbol, 1.0, entry, stop, one_lot_profit))
      return false;
   double one_lot_loss = MathAbs(one_lot_profit);
   if(one_lot_loss <= 0.0)
      return false;

   double effective_risk = MathMin(RiskPercent, HARD_MAX_RISK_PERCENT);
   double requested_risk =
      AccountInfoDouble(ACCOUNT_EQUITY) * effective_risk / 100.0;
   volume = NormalizeVolumeDown(requested_risk / one_lot_loss);
   if(volume <= 0.0)
      return false;

   double actual_stop_profit = 0.0;
   if(!OrderCalcProfit(order_type, TradeSymbol, volume, entry, stop, actual_stop_profit))
      return false;
   risk_money = MathAbs(actual_stop_profit);
   return risk_money > 0.0;
}

bool TradeResultAccepted()
{
   uint retcode = Trade.ResultRetcode();
   return (
      retcode == TRADE_RETCODE_DONE ||
      retcode == TRADE_RETCODE_DONE_PARTIAL ||
      retcode == TRADE_RETCODE_PLACED
   );
}

void DrawStatus(const string status, const string json)
{
   string action = JsonValue(json, "action");
   string confidence = JsonValue(json, "confidence");
   string score = JsonValue(json, "technical_score");
   string trend = JsonValue(json, "trend_m5");
   string momentum = JsonValue(json, "momentum_m1");
   string guard = JsonValue(json, "risk_guard_status");
   string spread_atr = JsonValue(json, "spread_to_atr");

   Comment(
      "FAST_SCALP_M1\n",
      "Status: ", status, "\n",
      "Symbol: ", TradeSymbol, " | M1\n",
      "Action: ", action, " | Confidence: ", confidence, "\n",
      "Score: ", score, " | M5: ", trend, " | M1 momentum: ", momentum, "\n",
      "Guard: ", guard, " | Spread/ATR: ", spread_atr, "\n",
      "Positions: ", PositionsTotal(), "/", EffectiveMaxOpenTrades(),
      " | Risk/trade: ", DoubleToString(RiskPercent, 2), "%\n",
      "Mode: ", (EnableAutoTrading && IsDemoAccount()) ? "ARMED DEMO" : "NO ORDERS"
   );
}

void MaybeTrade(const string json)
{
   if(!EnableAutoTrading || !IsDemoAccount())
      return;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
      return;

   datetime current_bar = iTime(TradeSymbol, PERIOD_M1, 0);
   if(current_bar <= 0 || current_bar == LastExecutedM1Bar)
      return;
   if(LastEntryTime > 0 && TimeCurrent() - LastEntryTime < CooldownSeconds)
      return;

   string action = JsonValue(json, "action");
   string symbol = JsonValue(json, "symbol");
   string news_risk = JsonValue(json, "news_risk");
   string guard = JsonValue(json, "risk_guard_status");
   int confidence = (int)StringToInteger(JsonValue(json, "confidence"));

   if(symbol != TradeSymbol)
      return;
   if(action != "BUY" && action != "SELL")
      return;
   if(confidence < MinConfidence)
      return;
   if(news_risk == "HIGH" || guard != "OK")
      return;

   long spread = SymbolInfoInteger(TradeSymbol, SYMBOL_SPREAD);
   if(MaxSpreadPoints > 0 && spread > MaxSpreadPoints)
      return;
   if(PositionsTotal() >= EffectiveMaxOpenTrades())
      return;
   if(ManagedOpenPositions() >= EffectiveMaxOpenTrades())
      return;
   if(!LocalDailyRiskAllows())
      return;

   MqlTick tick;
   if(!SymbolInfoTick(TradeSymbol, tick))
      return;

   double stop = 0.0;
   double target = 0.0;
   double risk_money = 0.0;
   double volume = 0.0;
   if(!BuildTradePlan(action, tick, stop, target, risk_money, volume))
      return;

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(TradeSymbol);

   bool request_ok = false;
   if(action == "BUY")
      request_ok = Trade.Buy(volume, TradeSymbol, 0.0, stop, target, "FAST_SCALP_M1");
   else
      request_ok = Trade.Sell(volume, TradeSymbol, 0.0, stop, target, "FAST_SCALP_M1");

   if(!request_ok || !TradeResultAccepted())
   {
      if(Verbose)
      {
         Print(
            "FAST_SCALP_M1 order failed. retcode=", Trade.ResultRetcode(),
            " ", Trade.ResultRetcodeDescription()
         );
      }
      return;
   }

   LastExecutedM1Bar = current_bar;
   LastEntryTime = TimeCurrent();
   if(Verbose)
   {
      Print(
         "FAST_SCALP_M1 opened ", action,
         " volume=", DoubleToString(volume, 3),
         " risk=$", DoubleToString(risk_money, 2),
         " SL=", DoubleToString(stop, _Digits),
         " TP=", DoubleToString(target, _Digits),
         " positions=", PositionsTotal(), "/", EffectiveMaxOpenTrades()
      );
   }
}

void RefreshSignal()
{
   string response = "";
   int status_code = -1;
   if(!FetchHint(response, status_code))
   {
      LastStatus = status_code >= 0 ?
         "HTTP " + IntegerToString(status_code) : "API ERROR";
      DrawStatus(LastStatus, "{}");
      if(Verbose)
         Print("FAST_SCALP_M1 hint unavailable. HTTP=", status_code, " error=", GetLastError());
      return;
   }

   LastPayload = response;
   LastStatus = "CONNECTED";
   MaybeTrade(response);
   DrawStatus(LastStatus, response);
}

int OnInit()
{
   if(_Symbol != TradeSymbol)
   {
      Alert("FAST_SCALP_M1: attach this EA to ", TradeSymbol, " only.");
      return INIT_FAILED;
   }
   if(_Period != PERIOD_M1)
   {
      Alert("FAST_SCALP_M1: attach this EA to an M1 chart.");
      return INIT_FAILED;
   }

   if(
      SnapshotBars < 30 || M5Bars < 20 || BridgeSeconds < 1 ||
      SignalSeconds < 1 || RequestTimeoutMs < 500 || MinConfidence < 0 ||
      MinConfidence > 100 || RiskPercent <= 0.0 ||
      RiskPercent > HARD_MAX_RISK_PERCENT || MaxDailyLossPercent <= 0.0 ||
      MaxOpenTrades < 1 || RewardRiskRatio <= 0.0 || AtrPeriod < 2 ||
      AtrMultiplier <= 0.0 || MinStopPoints < 1 ||
      MaxStopPoints < MinStopPoints || CooldownSeconds < 0
   )
      return INIT_PARAMETERS_INCORRECT;

   if(!SymbolSelect(TradeSymbol, true))
      return INIT_FAILED;

   AtrHandle = iATR(TradeSymbol, PERIOD_M1, AtrPeriod);
   if(AtrHandle == INVALID_HANDLE)
      return INIT_FAILED;

   if(EnableAutoTrading && !IsDemoAccount())
      Print("FAST_SCALP_M1: real/contest account detected; order placement is HARD BLOCKED.");
   if(MaxOpenTrades > 1 && !IsHedgingAccount())
      Print("FAST_SCALP_M1: netting account detected; effective max open trades is 1.");

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(TradeSymbol);

   EventSetTimer(1);
   WriteSnapshot();
   LastBridgeMs = GetTickCount64();
   RefreshSignal();
   LastSignalMs = GetTickCount64();

   Print(
      "FAST_SCALP_M1 ready on ", TradeSymbol,
      ". max_positions=", EffectiveMaxOpenTrades(),
      " risk_each=", DoubleToString(RiskPercent, 2),
      "% demo_auto=", EnableAutoTrading && IsDemoAccount()
   );
   return INIT_SUCCEEDED;
}

void OnTick()
{
}

void OnTimer()
{
   ulong now_ms = GetTickCount64();

   if(now_ms - LastBridgeMs >= (ulong)BridgeSeconds * 1000)
   {
      WriteSnapshot();
      LastBridgeMs = now_ms;
   }

   if(now_ms - LastSignalMs >= (ulong)SignalSeconds * 1000)
   {
      RefreshSignal();
      LastSignalMs = now_ms;
   }

   DrawStatus(LastStatus, LastPayload);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
   if(AtrHandle != INVALID_HANDLE)
   {
      IndicatorRelease(AtrHandle);
      AtrHandle = INVALID_HANDLE;
   }
}
