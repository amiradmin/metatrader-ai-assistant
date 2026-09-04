#property strict
#property description "ONE EA: bridge + readable panel + DEMO auto trader + demo journal + demo goal"

#include <Trade/Trade.mqh>

// -----------------------------------------------------------------------------
// One-chart setup
// -----------------------------------------------------------------------------
input string TradeSymbol = "XAUUSD_o";
input string ApiUrl = "http://127.0.0.1:8000/hint";
input int SnapshotBars = 100;
input int ContextBars = 100;
input int BridgeSeconds = 5;
input int SignalSeconds = 15;
input int RequestTimeoutMs = 45000;

// -----------------------------------------------------------------------------
// Readable panel + demo goal
// -----------------------------------------------------------------------------
input int PanelLeft = 25;
input int PanelTop = 120;
input int PanelWidth = 640;
input int PanelHeight = 445;
input int PanelFontSize = 14;
input double DemoDailyGoalUSD = 10.0;
input int DemoGoalWindowDays = 20;

// -----------------------------------------------------------------------------
// Demo execution
// -----------------------------------------------------------------------------
input bool EnableAutoTrading = true;
input int MinConfidence = 75;
input double RiskPercent = 0.5;
input double RewardRiskRatio = 2.0;
input int MaxSpreadPoints = 50;
input int MaxOpenTrades = 1;
input int SlippagePoints = 20;
input ulong MagicNumber = 26090315;

input int AtrPeriod = 14;
input double AtrMultiplier = 1.5;
input int MinStopPoints = 150;
input int MaxStopPoints = 1200;
input int SwingLookbackBars = 30;
input int SwingLeftBars = 2;
input int SwingRightBars = 2;
input int StructureBufferPoints = 50;
input bool UseAntiChase = true;
input double MaxExtensionAtr = 1.5;
input double PullbackZoneAtr = 0.35;
input int PullbackMaxBars = 4;

// -----------------------------------------------------------------------------
// Files
// -----------------------------------------------------------------------------
input string SnapshotFile = "mt5_snapshot.json";
input string ContextFile = "mt5_context.json";
input bool ExportJournal = true;
input int JournalHistoryDays = 180;
input int JournalSeconds = 30;
input string JournalFile = "demo_trade_journal.csv";
input bool Verbose = true;

const double HARD_MAX_RISK_PERCENT = 0.5;
string PanelPrefix = "MetaTraderAI_Panel_";

CTrade Trade;
int AtrHandle = INVALID_HANDLE;
int Ema9Handle = INVALID_HANDLE;
int Ema21Handle = INVALID_HANDLE;
ulong LastBridgeMs = 0;
ulong LastSignalMs = 0;
ulong LastJournalMs = 0;
datetime LastExecutedM15Bar = 0;
datetime PendingPullbackStartedBar = 0;
string PendingPullbackAction = "";
bool TradingArmed = false;
string LastApiPayload = "{}";
string LastPanelStatus = "STARTING";

double DemoTodayPnl = 0.0;
double DemoGoalAverage = 0.0;
double DemoGoalProgress = 0.0;
int DemoGoalObservedDays = 0;
string DemoGoalStatus = "COLLECTING 0/20";

struct PositionAggregate
{
   ulong position_id;
   datetime opened_at;
   datetime closed_at;
   string symbol;
   string side;
   double entry_volume;
   double exit_volume;
   double entry_price_value;
   double exit_price_value;
   double initial_sl;
   double initial_tp;
   double net_pnl;
};

bool IsDemoAccount()
{
   ENUM_ACCOUNT_TRADE_MODE mode =
      (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   return mode == ACCOUNT_TRADE_MODE_DEMO;
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

string BrokerTimestamp(const datetime value)
{
   if(value <= 0)
      return "";
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return StringFormat(
      "%04d-%02d-%02dT%02d:%02d:%02d",
      parts.year, parts.mon, parts.day,
      parts.hour, parts.min, parts.sec
   );
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

string ValueOr(const string value, const string fallback)
{
   if(value == "" || value == "null")
      return fallback;
   return value;
}

bool WriteTextFile(const string file_name, const string payload)
{
   int handle = FileOpen(file_name, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("MetaTraderAI: FileOpen failed for ", file_name, ": ", GetLastError());
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

string TimeframeJson(
   const string name,
   const ENUM_TIMEFRAMES timeframe,
   MqlRates &rates[],
   const int copied,
   const int digits
)
{
   string json = "\"" + name + "\":{";
   json += "\"timeframe\":\"" + EnumToString(timeframe) + "\",";
   json += RatesField(rates, copied, "opens", digits) + ",";
   json += RatesField(rates, copied, "highs", digits) + ",";
   json += RatesField(rates, copied, "lows", digits) + ",";
   json += RatesField(rates, copied, "closes", digits);
   json += "}";
   return json;
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

datetime BrokerDayStart()
{
   return DayStartOf(TimeCurrent());
}

bool IsWeekday(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return parts.day_of_week >= 1 && parts.day_of_week <= 5;
}

bool AccountDayRiskMetrics(double &realized_pnl, double &day_start_balance)
{
   realized_pnl = 0.0;
   day_start_balance = 0.0;
   if(!HistorySelect(BrokerDayStart(), TimeCurrent()))
      return false;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
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

bool IsManagedDeal(const ulong deal)
{
   if(deal == 0)
      return false;
   if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber)
      return false;
   if(HistoryDealGetString(deal, DEAL_SYMBOL) != TradeSymbol)
      return false;

   ENUM_DEAL_TYPE type =
      (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE);
   return type == DEAL_TYPE_BUY || type == DEAL_TYPE_SELL;
}

double DealNetPnl(const ulong deal)
{
   return
      HistoryDealGetDouble(deal, DEAL_PROFIT) +
      HistoryDealGetDouble(deal, DEAL_COMMISSION) +
      HistoryDealGetDouble(deal, DEAL_SWAP) +
      HistoryDealGetDouble(deal, DEAL_FEE);
}

bool UpdateDemoGoalStats()
{
   DemoTodayPnl = 0.0;
   DemoGoalAverage = 0.0;
   DemoGoalProgress = 0.0;
   DemoGoalObservedDays = 0;
   DemoGoalStatus = "COLLECTING 0/" + IntegerToString(DemoGoalWindowDays);

   if(!IsDemoAccount())
   {
      DemoGoalStatus = "DEMO ONLY";
      return true;
   }

   datetime now = TimeCurrent();
   datetime today_start = BrokerDayStart();
   datetime history_start =
      today_start - (datetime)(MathMax(JournalHistoryDays, 90) * 86400);

   if(!HistorySelect(history_start, now))
      return false;

   int total = HistoryDealsTotal();
   datetime earliest_managed = 0;

   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(!IsManagedDeal(deal))
         continue;

      datetime deal_time =
         (datetime)HistoryDealGetInteger(deal, DEAL_TIME);

      if(deal_time >= today_start)
         DemoTodayPnl += DealNetPnl(deal);

      if(earliest_managed == 0 || deal_time < earliest_managed)
         earliest_managed = deal_time;
   }

   if(earliest_managed == 0)
      return true;

   datetime first_day = DayStartOf(earliest_managed);
   int available_completed_days = 0;

   for(datetime day = first_day; day < today_start; day += 86400)
   {
      if(IsWeekday(day))
         available_completed_days++;
   }

   int target_days = MathMin(available_completed_days, DemoGoalWindowDays);
   if(target_days <= 0)
      return true;

   double window_pnl = 0.0;
   int counted_days = 0;

   for(
      datetime day = today_start - 86400;
      day >= first_day && counted_days < target_days;
      day -= 86400
   )
   {
      if(!IsWeekday(day))
         continue;

      datetime day_end = day + 86400;
      double day_pnl = 0.0;

      for(int i = 0; i < total; i++)
      {
         ulong deal = HistoryDealGetTicket(i);
         if(!IsManagedDeal(deal))
            continue;

         datetime deal_time =
            (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
         if(deal_time >= day && deal_time < day_end)
            day_pnl += DealNetPnl(deal);
      }

      window_pnl += day_pnl;
      counted_days++;
   }

   DemoGoalObservedDays = counted_days;
   DemoGoalAverage = window_pnl / counted_days;

   if(DemoDailyGoalUSD > 0.0)
      DemoGoalProgress = MathMax(0.0, DemoGoalAverage / DemoDailyGoalUSD * 100.0);

   if(counted_days < DemoGoalWindowDays)
   {
      DemoGoalStatus =
         "COLLECTING " + IntegerToString(counted_days) +
         "/" + IntegerToString(DemoGoalWindowDays);
   }
   else if(DemoGoalAverage >= DemoDailyGoalUSD)
   {
      DemoGoalStatus = "GOAL MET";
   }
   else
   {
      DemoGoalStatus = "BUILDING";
   }

   return true;
}

bool WriteSnapshot()
{
   MqlTick tick;
   if(!SymbolInfoTick(TradeSymbol, tick))
      return false;

   int digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   MqlRates rates[];
   int copied = 0;
   if(!CopyCompletedRates(PERIOD_M15, SnapshotBars, rates, copied, 21))
      return false;

   double day_realized_pnl = 0.0;
   double day_start_balance = 0.0;
   bool has_day_metrics =
      AccountDayRiskMetrics(day_realized_pnl, day_start_balance);

   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(TradeSymbol) + "\",";
   json += "\"timeframe\":\"PERIOD_M15\",";
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
   json += RatesField(rates, copied, "opens", digits) + ",";
   json += RatesField(rates, copied, "highs", digits) + ",";
   json += RatesField(rates, copied, "lows", digits) + ",";
   json += RatesField(rates, copied, "closes", digits);
   json += "}";
   return WriteTextFile(SnapshotFile, json);
}

bool WriteHigherTimeframeContext()
{
   int digits = (int)SymbolInfoInteger(TradeSymbol, SYMBOL_DIGITS);
   MqlRates h1[];
   MqlRates h4[];
   int copied_h1 = 0;
   int copied_h4 = 0;

   if(!CopyCompletedRates(PERIOD_H1, ContextBars, h1, copied_h1, 65))
      return false;
   if(!CopyCompletedRates(PERIOD_H4, ContextBars, h4, copied_h4, 65))
      return false;

   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(TradeSymbol) + "\",";
   json += "\"generated_at\":\"" + UtcIsoTimestamp() + "\",";
   json += TimeframeJson("h1", PERIOD_H1, h1, copied_h1, digits) + ",";
   json += TimeframeJson("h4", PERIOD_H4, h4, copied_h4, digits);
   json += "}";
   return WriteTextFile(ContextFile, json);
}

bool RefreshBridge()
{
   bool snapshot_ok = WriteSnapshot();
   bool context_ok = WriteHigherTimeframeContext();
   return snapshot_ok && context_ok;
}

void SetPanelBackground()
{
   string name = PanelPrefix + "BG";
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, PanelLeft);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, PanelTop);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, PanelWidth);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, PanelHeight);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, (long)ColorToARGB(C'20,24,31', 191));
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, C'85,95,110');
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

void SetPanelLine(
   const string id,
   const string text,
   const int y,
   const int font_size,
   const color text_color
)
{
   string name = PanelPrefix + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, PanelLeft + 20);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, PanelTop + y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, text_color);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

color ActionColor(const string action)
{
   if(action == "BUY") return clrLime;
   if(action == "SELL") return clrTomato;
   return clrGold;
}

color GuardColor(const string guard)
{
   if(guard == "OK") return clrLime;
   if(guard == "DAILY_LOSS_LIMIT" || guard == "DAILY_RISK_BUDGET_EXHAUSTED")
      return clrTomato;
   return clrGold;
}

color GoalColor()
{
   if(DemoGoalStatus == "GOAL MET")
      return clrLime;
   if(DemoGoalAverage < 0.0 && DemoGoalObservedDays > 0)
      return clrTomato;
   return clrGold;
}

void DrawPanel(const string status, const string json)
{
   string action = ValueOr(JsonValue(json, "action"), "WAIT");
   string confidence = ValueOr(JsonValue(json, "confidence"), "0");
   string technical = ValueOr(JsonValue(json, "technical_score"), "0");
   string h1 = ValueOr(JsonValue(json, "h1_trend"), "UNAVAILABLE");
   string h4 = ValueOr(JsonValue(json, "h4_trend"), "UNAVAILABLE");
   string mtf = ValueOr(JsonValue(json, "mtf_status"), "UNAVAILABLE");
   string news = ValueOr(JsonValue(json, "news_risk"), "UNKNOWN");
   string coverage = ValueOr(JsonValue(json, "news_coverage"), "UNAVAILABLE");
   string guard = ValueOr(JsonValue(json, "risk_guard_status"), "UNAVAILABLE");
   string daily_dd = ValueOr(JsonValue(json, "day_drawdown_percent"), "-");
   string spread_atr = ValueOr(JsonValue(json, "spread_to_atr"), "-");
   string generated_at = ValueOr(JsonValue(json, "generated_at"), "-");
   string auto_state = TradingArmed ? "ARMED DEMO" : "OFF";

   SetPanelBackground();
   SetPanelLine("Hello", "Hello Amir", 14, PanelFontSize + 1, clrWhite);
   SetPanelLine("Title", "META TRADER AI  |  ONE EA", 46, PanelFontSize + 4, clrDeepSkyBlue);
   SetPanelLine("Status", "Status: " + status + "   |   Auto: " + auto_state, 86, PanelFontSize, clrWhite);
   SetPanelLine("Symbol", "Symbol: " + TradeSymbol + "   |   M15", 118, PanelFontSize, clrWhite);
   SetPanelLine("Decision", "Decision: " + action, 152, PanelFontSize + 3, ActionColor(action));
   SetPanelLine(
      "Confidence",
      "Confidence: " + confidence + " / 100   |   Min: " + IntegerToString(MinConfidence),
      190,
      PanelFontSize,
      clrWhite
   );
   SetPanelLine(
      "Technical",
      "Technical: " + technical + "   |   H1: " + h1 + "   |   H4: " + h4 + "   |   MTF: " + mtf,
      224,
      PanelFontSize - 1,
      clrWhite
   );
   SetPanelLine(
      "News",
      "News: " + news + "   |   Coverage: " + coverage,
      258,
      PanelFontSize,
      news == "HIGH" ? clrTomato : clrWhite
   );
   SetPanelLine(
      "Risk",
      "Risk guard: " + guard + "   |   Daily DD: " + daily_dd + "%   |   Spread/ATR: " + spread_atr,
      292,
      PanelFontSize,
      GuardColor(guard)
   );
   SetPanelLine(
      "Goal",
      "Demo goal: $" + DoubleToString(DemoDailyGoalUSD, 2) + "/day   |   Today: " +
         (DemoTodayPnl >= 0.0 ? "+$" : "-$") + DoubleToString(MathAbs(DemoTodayPnl), 2),
      328,
      PanelFontSize,
      GoalColor()
   );
   SetPanelLine(
      "GoalState",
      IntegerToString(DemoGoalWindowDays) + "D avg: " +
         (DemoGoalAverage >= 0.0 ? "+$" : "-$") + DoubleToString(MathAbs(DemoGoalAverage), 2) +
         "/day   |   Progress: " + DoubleToString(DemoGoalProgress, 0) + "%   |   " + DemoGoalStatus,
      362,
      PanelFontSize - 1,
      GoalColor()
   );
   SetPanelLine("Time", "UTC: " + generated_at, 402, PanelFontSize - 2, clrSilver);
   ChartRedraw();
}

void DeletePanel()
{
   string ids[] = {
      "BG", "Hello", "Title", "Status", "Symbol", "Decision",
      "Confidence", "Technical", "News", "Risk", "Goal", "GoalState", "Time"
   };
   for(int i = 0; i < ArraySize(ids); i++)
      ObjectDelete(0, PanelPrefix + ids[i]);
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
   if(requested + 1e-12 < minimum) return 0.0;

   double value = MathMin(maximum, requested);
   if(step > 0.0)
      value = minimum + MathFloor((value - minimum + 1e-12) / step) * step;
   if(value + 1e-12 < minimum) return 0.0;
   return NormalizeDouble(value, VolumeDigits(step));
}

int ManagedOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if(
         PositionGetString(POSITION_SYMBOL) == TradeSymbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == MagicNumber
      )
         count++;
   }
   return count;
}

bool GetCompletedIndicatorValue(const int handle, double &value)
{
   value = 0.0;
   if(handle == INVALID_HANDLE) return false;
   double values[];
   ArraySetAsSeries(values, true);
   if(CopyBuffer(handle, 0, 1, 1, values) != 1) return false;
   value = values[0];
   return value > 0.0;
}

bool FindRecentConfirmedSwing(
   const string action,
   const ENUM_TIMEFRAMES timeframe,
   const int lookback_bars,
   const int left_bars,
   const int right_bars,
   double &swing_price
)
{
   swing_price = 0.0;
   if(lookback_bars < left_bars + right_bars + 3) return false;

   int first_shift = right_bars + 1;
   for(int shift = first_shift; shift <= lookback_bars; shift++)
   {
      double candidate =
         action == "BUY" ? iLow(TradeSymbol, timeframe, shift) : iHigh(TradeSymbol, timeframe, shift);
      if(candidate <= 0.0) continue;

      bool confirmed = true;
      for(int offset = 1; offset <= left_bars && confirmed; offset++)
      {
         double older =
            action == "BUY" ? iLow(TradeSymbol, timeframe, shift + offset) : iHigh(TradeSymbol, timeframe, shift + offset);
         if(older <= 0.0) { confirmed = false; break; }
         if(action == "BUY" && candidate >= older) confirmed = false;
         if(action == "SELL" && candidate <= older) confirmed = false;
      }

      for(int offset = 1; offset <= right_bars && confirmed; offset++)
      {
         double newer =
            action == "BUY" ? iLow(TradeSymbol, timeframe, shift - offset) : iHigh(TradeSymbol, timeframe, shift - offset);
         if(newer <= 0.0) { confirmed = false; break; }
         if(action == "BUY" && candidate > newer) confirmed = false;
         if(action == "SELL" && candidate < newer) confirmed = false;
      }

      if(confirmed)
      {
         swing_price = candidate;
         return true;
      }
   }
   return false;
}

void ResetPendingPullback()
{
   PendingPullbackAction = "";
   PendingPullbackStartedBar = 0;
}

bool EntryTimingAllows(
   const string action,
   const datetime current_bar,
   bool &pullback_reentry
)
{
   pullback_reentry = false;
   if(!UseAntiChase) return true;

   MqlTick tick;
   if(!SymbolInfoTick(TradeSymbol, tick)) return false;

   double atr = 0.0;
   double ema9 = 0.0;
   double ema21 = 0.0;
   if(!GetCompletedIndicatorValue(AtrHandle, atr)) return false;
   if(!GetCompletedIndicatorValue(Ema9Handle, ema9)) return false;
   if(!GetCompletedIndicatorValue(Ema21Handle, ema21)) return false;

   double entry = action == "BUY" ? tick.ask : tick.bid;
   double extension_atr =
      action == "BUY" ? (entry - ema21) / atr : (ema21 - entry) / atr;

   if(PendingPullbackAction != "" && PendingPullbackAction != action)
      ResetPendingPullback();

   if(PendingPullbackAction == "")
   {
      if(extension_atr > MaxExtensionAtr)
      {
         PendingPullbackAction = action;
         PendingPullbackStartedBar = current_bar;
         if(Verbose)
            Print("MetaTraderAI anti-chase: waiting for pullback. extension=", DoubleToString(extension_atr, 2), " ATR");
         return false;
      }
      return true;
   }

   int shift = iBarShift(TradeSymbol, PERIOD_M15, PendingPullbackStartedBar, false);
   if(shift < 0 || shift > PullbackMaxBars)
   {
      ResetPendingPullback();
      return false;
   }
   if(extension_atr > MaxExtensionAtr) return false;

   bool trend_aligned;
   double distance_atr;
   bool reclaimed;
   if(action == "BUY")
   {
      trend_aligned = ema9 > ema21;
      distance_atr = (entry - ema9) / atr;
      reclaimed = entry >= ema9 && entry >= ema21;
   }
   else
   {
      trend_aligned = ema9 < ema21;
      distance_atr = (ema9 - entry) / atr;
      reclaimed = entry <= ema9 && entry <= ema21;
   }

   bool in_zone = distance_atr >= 0.0 && distance_atr <= PullbackZoneAtr;
   if(trend_aligned && reclaimed && in_zone)
   {
      pullback_reentry = true;
      ResetPendingPullback();
      return true;
   }
   return false;
}

bool BuildTradePlan(
   const string action,
   const MqlTick &tick,
   double &entry,
   double &stop,
   double &target,
   double &risk_money,
   double &volume
)
{
   double point = SymbolInfoDouble(TradeSymbol, SYMBOL_POINT);
   if(point <= 0.0) return false;

   double atr = 0.0;
   if(!GetCompletedIndicatorValue(AtrHandle, atr)) return false;
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

   ENUM_ORDER_TYPE order_type = action == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double one_lot_profit = 0.0;
   if(!OrderCalcProfit(order_type, TradeSymbol, 1.0, entry, stop, one_lot_profit))
      return false;

   double one_lot_loss = MathAbs(one_lot_profit);
   if(one_lot_loss <= 0.0) return false;

   double effective_risk = MathMin(RiskPercent, HARD_MAX_RISK_PERCENT);
   risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * effective_risk / 100.0;
   volume = NormalizeVolumeDown(risk_money / one_lot_loss);
   return volume > 0.0;
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

void MaybeTrade(const string json)
{
   if(!TradingArmed) return;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
      return;

   datetime current_bar = iTime(TradeSymbol, PERIOD_M15, 0);
   if(current_bar <= 0 || current_bar == LastExecutedM15Bar) return;

   string action = JsonValue(json, "action");
   string symbol = JsonValue(json, "symbol");
   string news_risk = JsonValue(json, "news_risk");
   int confidence = (int)StringToInteger(JsonValue(json, "confidence"));

   if(symbol != TradeSymbol) return;
   if(action != "BUY" && action != "SELL") return;
   if(confidence < MinConfidence) return;
   if(news_risk == "HIGH") return;

   long spread = SymbolInfoInteger(TradeSymbol, SYMBOL_SPREAD);
   if(MaxSpreadPoints > 0 && spread > MaxSpreadPoints) return;
   if(ManagedOpenPositions() >= MaxOpenTrades) return;

   bool pullback_reentry = false;
   if(!EntryTimingAllows(action, current_bar, pullback_reentry)) return;

   MqlTick tick;
   if(!SymbolInfoTick(TradeSymbol, tick)) return;

   double entry = 0.0;
   double stop = 0.0;
   double target = 0.0;
   double risk_money = 0.0;
   double volume = 0.0;
   if(!BuildTradePlan(action, tick, entry, stop, target, risk_money, volume))
      return;

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(TradeSymbol);

   bool request_ok = false;
   if(action == "BUY")
      request_ok = Trade.Buy(volume, TradeSymbol, 0.0, stop, target, "MetaTraderAI");
   else
      request_ok = Trade.Sell(volume, TradeSymbol, 0.0, stop, target, "MetaTraderAI");

   if(!request_ok || !TradeResultAccepted())
   {
      Print("MetaTraderAI order failed. retcode=", Trade.ResultRetcode(), " ", Trade.ResultRetcodeDescription());
      return;
   }

   LastExecutedM15Bar = current_bar;
   if(Verbose)
   {
      Print(
         "MetaTraderAI opened ", action,
         " volume=", DoubleToString(volume, 3),
         " risk=$", DoubleToString(risk_money, 2),
         " SL=", DoubleToString(stop, _Digits),
         " TP=", DoubleToString(target, _Digits),
         " pullback=", pullback_reentry
      );
   }
}

int FindPosition(PositionAggregate &items[], const ulong position_id)
{
   for(int i = 0; i < ArraySize(items); i++)
      if(items[i].position_id == position_id) return i;
   return -1;
}

int FindOrAddPosition(PositionAggregate &items[], const ulong position_id)
{
   int existing = FindPosition(items, position_id);
   if(existing >= 0) return existing;

   int index = ArraySize(items);
   ArrayResize(items, index + 1);
   items[index].position_id = position_id;
   items[index].opened_at = 0;
   items[index].closed_at = 0;
   items[index].symbol = "";
   items[index].side = "";
   items[index].entry_volume = 0.0;
   items[index].exit_volume = 0.0;
   items[index].entry_price_value = 0.0;
   items[index].exit_price_value = 0.0;
   items[index].initial_sl = 0.0;
   items[index].initial_tp = 0.0;
   items[index].net_pnl = 0.0;
   return index;
}

bool InitialRiskMoney(
   PositionAggregate &item,
   const double entry_price,
   double &risk_money
)
{
   risk_money = 0.0;
   if(item.initial_sl <= 0.0 || item.entry_volume <= 0.0 || entry_price <= 0.0)
      return false;

   ENUM_ORDER_TYPE order_type = item.side == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double stop_profit = 0.0;
   if(!OrderCalcProfit(
      order_type, item.symbol, item.entry_volume,
      entry_price, item.initial_sl, stop_profit
   ))
      return false;

   risk_money = MathAbs(stop_profit);
   return risk_money > 0.0;
}

bool BuildDemoJournal()
{
   if(!ExportJournal || !IsDemoAccount()) return true;

   datetime now = TimeCurrent();
   datetime from = now - (datetime)(MathMax(1, JournalHistoryDays) * 86400);
   if(!HistorySelect(from, now)) return false;

   PositionAggregate items[];
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber) continue;

      string symbol = HistoryDealGetString(deal, DEAL_SYMBOL);
      if(symbol != TradeSymbol) continue;

      ENUM_DEAL_TYPE deal_type =
         (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE);
      if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL) continue;

      ulong position_id =
         (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      if(position_id == 0) continue;

      ENUM_DEAL_ENTRY entry_kind =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
      int index = FindOrAddPosition(items, position_id);
      double volume = HistoryDealGetDouble(deal, DEAL_VOLUME);
      double price = HistoryDealGetDouble(deal, DEAL_PRICE);
      datetime deal_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);

      items[index].symbol = symbol;
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_PROFIT);
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_SWAP);
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_FEE);

      if(entry_kind == DEAL_ENTRY_IN)
      {
         items[index].entry_volume += volume;
         items[index].entry_price_value += price * volume;
         if(items[index].opened_at == 0 || deal_time < items[index].opened_at)
            items[index].opened_at = deal_time;
         if(items[index].side == "")
            items[index].side = deal_type == DEAL_TYPE_BUY ? "BUY" : "SELL";

         if(items[index].initial_sl <= 0.0)
         {
            ulong order_ticket = (ulong)HistoryDealGetInteger(deal, DEAL_ORDER);
            if(order_ticket > 0)
            {
               items[index].initial_sl = HistoryOrderGetDouble(order_ticket, ORDER_SL);
               items[index].initial_tp = HistoryOrderGetDouble(order_ticket, ORDER_TP);
            }
         }
      }
      else if(entry_kind == DEAL_ENTRY_OUT || entry_kind == DEAL_ENTRY_OUT_BY)
      {
         items[index].exit_volume += volume;
         items[index].exit_price_value += price * volume;
         if(deal_time > items[index].closed_at)
            items[index].closed_at = deal_time;
      }
   }

   int handle = FileOpen(JournalFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE) return false;

   FileWrite(
      handle,
      "position_id", "opened_at_broker", "closed_at_broker",
      "symbol", "side", "volume", "entry_price", "exit_price",
      "initial_sl", "initial_tp", "net_pnl", "planned_risk_money",
      "pnl_r", "outcome", "magic"
   );

   int written = 0;
   for(int i = 0; i < ArraySize(items); i++)
   {
      PositionAggregate item = items[i];
      if(item.entry_volume <= 0.0) continue;

      double volume_step = SymbolInfoDouble(item.symbol, SYMBOL_VOLUME_STEP);
      double epsilon = MathMax(1e-8, volume_step / 2.0);
      if(item.closed_at <= 0 || item.exit_volume + epsilon < item.entry_volume)
         continue;

      double entry_price = item.entry_price_value / item.entry_volume;
      double exit_price = item.exit_volume > 0.0 ? item.exit_price_value / item.exit_volume : 0.0;
      double risk_money = 0.0;
      bool has_risk = InitialRiskMoney(item, entry_price, risk_money);
      string pnl_r = has_risk ? DoubleToString(item.net_pnl / risk_money, 6) : "";
      string outcome = "FLAT";
      if(item.net_pnl > 1e-8) outcome = "WIN";
      else if(item.net_pnl < -1e-8) outcome = "LOSS";

      int digits = (int)SymbolInfoInteger(item.symbol, SYMBOL_DIGITS);
      string initial_sl_text = item.initial_sl > 0.0 ? DoubleToString(item.initial_sl, digits) : "";
      string initial_tp_text = item.initial_tp > 0.0 ? DoubleToString(item.initial_tp, digits) : "";
      string risk_text = has_risk ? DoubleToString(risk_money, 2) : "";

      FileWrite(
         handle,
         StringFormat("%I64u", item.position_id),
         BrokerTimestamp(item.opened_at),
         BrokerTimestamp(item.closed_at),
         item.symbol, item.side,
         DoubleToString(item.entry_volume, 4),
         DoubleToString(entry_price, digits),
         DoubleToString(exit_price, digits),
         initial_sl_text, initial_tp_text,
         DoubleToString(item.net_pnl, 2),
         risk_text, pnl_r, outcome,
         StringFormat("%I64u", MagicNumber)
      );
      written++;
   }

   FileClose(handle);
   if(Verbose)
      Print("MetaTraderAI journal: wrote ", written, " closed trades.");
   return true;
}

void RefreshSignal()
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
      if(Verbose)
         Print("MetaTraderAI hint unavailable. HTTP=", status_code, " last_error=", GetLastError());
      return;
   }

   LastApiPayload = response;
   LastPanelStatus = "CONNECTED";
   DrawPanel("CONNECTED", response);
   MaybeTrade(response);
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
      MinConfidence < 0 || MinConfidence > 100 ||
      RiskPercent <= 0.0 || RiskPercent > HARD_MAX_RISK_PERCENT ||
      RewardRiskRatio <= 0.0 || AtrPeriod < 2 || AtrMultiplier <= 0.0 ||
      MinStopPoints < 1 || MaxStopPoints < MinStopPoints || MaxOpenTrades < 1
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

   Comment("");
   DeletePanel();
   EventSetTimer(1);

   RefreshBridge();
   LastBridgeMs = GetTickCount64();
   BuildDemoJournal();
   UpdateDemoGoalStats();
   LastJournalMs = GetTickCount64();
   DrawPanel("CONNECTING", "{}");
   RefreshSignal();
   LastSignalMs = GetTickCount64();

   Print("MetaTraderAI ready: ONE EA on ", TradeSymbol, " M15. demo_auto=", TradingArmed);
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   ulong now_ms = GetTickCount64();

   if(now_ms - LastBridgeMs >= (ulong)BridgeSeconds * 1000)
   {
      RefreshBridge();
      LastBridgeMs = now_ms;
   }

   if(now_ms - LastSignalMs >= (ulong)SignalSeconds * 1000)
   {
      RefreshSignal();
      LastSignalMs = now_ms;
   }

   if(ExportJournal && now_ms - LastJournalMs >= (ulong)JournalSeconds * 1000)
   {
      BuildDemoJournal();
      UpdateDemoGoalStats();
      LastJournalMs = now_ms;
   }

   DrawPanel(LastPanelStatus, LastApiPayload);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DeletePanel();
   Comment("");
   ResetPendingPullback();

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
   ChartRedraw();
}
