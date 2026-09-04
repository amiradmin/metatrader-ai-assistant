#ifndef META_TRADER_AI_NY_SESSION_TRACKER_MQH
#define META_TRADER_AI_NY_SESSION_TRACKER_MQH

// -----------------------------------------------------------------------------
// New York session observer. Read-only analytics: it never calls CTrade and
// never places/modifies/closes an MT5 order.
// Session times are expressed in US Eastern Time and DST is resolved using the
// current US rule: second Sunday in March -> first Sunday in November.
// -----------------------------------------------------------------------------
input bool EnableNYSessionTracker = true;
input int NYSessionStartHourET = 8;
input int NYSessionStartMinuteET = 0;
input int NYSessionEndHourET = 17;
input int NYSessionEndMinuteET = 0;
input string NYSessionTrackerFile = "ny_session_tracker.csv";
input int NYPanelRight = 20;
input int NYPanelTop = 30;
input int NYPanelWidth = 465;
input int NYPanelHeight = 260;
input int NYPanelFontSize = 10;

string NYPanelPrefix = "MetaTraderAI_NY_";
string NYSessionKey = "";
datetime NYLastCandidateBar = 0;
string NYLastCandidateAction = "";
string NYLastEvent = "No NY candidate yet";

int NYCandidates = 0;
int NYStrictOpened = 0;
int NYStrictWaitPullback = 0;
int NYRejectConfidence = 0;
int NYRejectNews = 0;
int NYRejectSpread = 0;
int NYRejectRisk = 0;
int NYRejectMaxOpen = 0;
int NYRejectOther = 0;
int NYShadowAEligible = 0;
int NYShadowAOpened = 0;
int NYShadowBEligible = 0;
int NYShadowBOpened = 0;
int NYStrictClosed = 0;
double NYStrictPnl = 0.0;

int NYNthSundayDay(const int year, const int month, const int nth)
{
   MqlDateTime parts;
   parts.year = year;
   parts.mon = month;
   parts.day = 1;
   parts.hour = 0;
   parts.min = 0;
   parts.sec = 0;
   parts.day_of_week = 0;
   parts.day_of_year = 0;

   datetime first = StructToTime(parts);
   MqlDateTime normalized;
   TimeToStruct(first, normalized);
   int first_sunday = 1 + ((7 - normalized.day_of_week) % 7);
   return first_sunday + (nth - 1) * 7;
}

datetime NYDstStartUtc(const int year)
{
   MqlDateTime parts;
   parts.year = year;
   parts.mon = 3;
   parts.day = NYNthSundayDay(year, 3, 2);
   parts.hour = 7; // 02:00 EST -> 07:00 UTC
   parts.min = 0;
   parts.sec = 0;
   parts.day_of_week = 0;
   parts.day_of_year = 0;
   return StructToTime(parts);
}

datetime NYDstEndUtc(const int year)
{
   MqlDateTime parts;
   parts.year = year;
   parts.mon = 11;
   parts.day = NYNthSundayDay(year, 11, 1);
   parts.hour = 6; // 02:00 EDT -> 06:00 UTC
   parts.min = 0;
   parts.sec = 0;
   parts.day_of_week = 0;
   parts.day_of_year = 0;
   return StructToTime(parts);
}

int NYEasternOffsetHours(const datetime utc_time)
{
   MqlDateTime parts;
   TimeToStruct(utc_time, parts);
   datetime start = NYDstStartUtc(parts.year);
   datetime ending = NYDstEndUtc(parts.year);
   return (utc_time >= start && utc_time < ending) ? -4 : -5;
}

void NYSessionBounds(
   const datetime utc_now,
   datetime &et_now,
   datetime &start_utc,
   datetime &end_utc,
   string &session_key
)
{
   int offset_hours = NYEasternOffsetHours(utc_now);
   et_now = utc_now + (datetime)(offset_hours * 3600);
   datetime et_day_start = DayStartOf(et_now);
   start_utc =
      et_day_start +
      (datetime)(NYSessionStartHourET * 3600 + NYSessionStartMinuteET * 60) -
      (datetime)(offset_hours * 3600);
   end_utc =
      et_day_start +
      (datetime)(NYSessionEndHourET * 3600 + NYSessionEndMinuteET * 60) -
      (datetime)(offset_hours * 3600);
   session_key = TimeToString(et_day_start, TIME_DATE);
}

bool NYIsWeekdayEt(const datetime et_now)
{
   MqlDateTime parts;
   TimeToStruct(et_now, parts);
   return parts.day_of_week >= 1 && parts.day_of_week <= 5;
}

string NYSessionStatus(
   const datetime utc_now,
   datetime &et_now,
   datetime &start_utc,
   datetime &end_utc,
   string &session_key
)
{
   NYSessionBounds(utc_now, et_now, start_utc, end_utc, session_key);
   if(!NYIsWeekdayEt(et_now))
      return "WEEKEND";
   if(utc_now < start_utc)
      return "PRE-OPEN";
   if(utc_now <= end_utc)
      return "OPEN";
   return "CLOSED";
}

void NYResetCounters(const string session_key)
{
   NYSessionKey = session_key;
   NYLastCandidateBar = 0;
   NYLastCandidateAction = "";
   NYLastEvent = "No NY candidate yet";
   NYCandidates = 0;
   NYStrictOpened = 0;
   NYStrictWaitPullback = 0;
   NYRejectConfidence = 0;
   NYRejectNews = 0;
   NYRejectSpread = 0;
   NYRejectRisk = 0;
   NYRejectMaxOpen = 0;
   NYRejectOther = 0;
   NYShadowAEligible = 0;
   NYShadowAOpened = 0;
   NYShadowBEligible = 0;
   NYShadowBOpened = 0;
   NYStrictClosed = 0;
   NYStrictPnl = 0.0;
}

int NYManagedOpenPositions()
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

void NYInitializeLog()
{
   if(!EnableNYSessionTracker)
      return;

   int handle = FileOpen(
      NYSessionTrackerFile,
      FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI,
      ','
   );
   if(handle == INVALID_HANDLE)
      handle = FileOpen(NYSessionTrackerFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("MetaTraderAI NY tracker: cannot open log. error=", GetLastError());
      return;
   }

   if(FileSize(handle) == 0)
   {
      FileWrite(
         handle,
         "utc_time", "et_time", "session_date_et", "m15_bar_broker",
         "action", "confidence", "technical_score",
         "strict_status", "strict_reason",
         "shadow_a_threshold", "shadow_a_status",
         "shadow_b_threshold", "shadow_b_status",
         "news_risk", "risk_guard", "spread_points"
      );
   }
   FileClose(handle);
}

void NYAppendLog(
   const datetime utc_now,
   const datetime et_now,
   const string session_key,
   const datetime current_bar,
   const string action,
   const int confidence,
   const int technical_score,
   const string strict_status,
   const string strict_reason,
   const string shadow_a_status,
   const string shadow_b_status,
   const string news_risk,
   const string risk_guard,
   const long spread_points
)
{
   int handle = FileOpen(
      NYSessionTrackerFile,
      FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI,
      ','
   );
   if(handle == INVALID_HANDLE)
      return;
   FileSeek(handle, 0, SEEK_END);
   FileWrite(
      handle,
      TimeToString(utc_now, TIME_DATE | TIME_SECONDS),
      TimeToString(et_now, TIME_DATE | TIME_SECONDS),
      session_key,
      BrokerTimestamp(current_bar),
      action,
      confidence,
      technical_score,
      strict_status,
      strict_reason,
      ShadowA.min_confidence,
      shadow_a_status,
      ShadowB.min_confidence,
      shadow_b_status,
      news_risk,
      risk_guard,
      spread_points
   );
   FileClose(handle);
}

string NYShadowStatus(ShadowState &state, const string action, const int confidence, const string news_risk, const datetime current_bar)
{
   if(!EnableShadowMode)
      return "OFF";
   if(confidence < state.min_confidence)
      return "REJECT_CONF";
   if(news_risk == "HIGH")
      return "REJECT_NEWS";
   if(state.last_executed_m15_bar == current_bar)
      return "OPENED";
   if(state.pending_action == action)
      return "WAIT_PULLBACK";
   return "FILTERED";
}

void NYTrackerOnSignal(const string json)
{
   if(!EnableNYSessionTracker)
      return;

   datetime utc_now = TimeGMT();
   datetime et_now = 0;
   datetime start_utc = 0;
   datetime end_utc = 0;
   string session_key = "";
   string session_status = NYSessionStatus(
      utc_now, et_now, start_utc, end_utc, session_key
   );

   if(session_key != NYSessionKey)
      NYResetCounters(session_key);
   if(session_status != "OPEN")
      return;

   string action = JsonValue(json, "action");
   if(action != "BUY" && action != "SELL")
      return;

   datetime current_bar = iTime(TradeSymbol, PERIOD_M15, 0);
   if(current_bar <= 0)
      return;
   if(current_bar == NYLastCandidateBar && action == NYLastCandidateAction)
      return;

   NYLastCandidateBar = current_bar;
   NYLastCandidateAction = action;
   NYCandidates++;

   int confidence = (int)StringToInteger(JsonValue(json, "confidence"));
   int technical = (int)StringToInteger(JsonValue(json, "technical_score"));
   string news_risk = ValueOr(JsonValue(json, "news_risk"), "UNKNOWN");
   string risk_guard = ValueOr(JsonValue(json, "risk_guard_status"), "UNAVAILABLE");
   long spread = SymbolInfoInteger(TradeSymbol, SYMBOL_SPREAD);

   string strict_status = "REJECTED";
   string strict_reason = "OTHER_GATE";

   if(LastExecutedM15Bar == current_bar)
   {
      strict_status = "OPENED";
      strict_reason = "TRADE";
      NYStrictOpened++;
   }
   else if(!TradingArmed)
   {
      strict_reason = "AUTO_OFF";
      NYRejectOther++;
   }
   else if(confidence < MinConfidence)
   {
      strict_reason = "CONF<" + IntegerToString(MinConfidence);
      NYRejectConfidence++;
   }
   else if(news_risk == "HIGH")
   {
      strict_reason = "NEWS_HIGH";
      NYRejectNews++;
   }
   else if(risk_guard != "OK" && risk_guard != "UNAVAILABLE" && risk_guard != "")
   {
      strict_reason = risk_guard;
      NYRejectRisk++;
   }
   else if(MaxSpreadPoints > 0 && spread > MaxSpreadPoints)
   {
      strict_reason = "SPREAD";
      NYRejectSpread++;
   }
   else if(NYManagedOpenPositions() >= MaxOpenTrades)
   {
      strict_reason = "MAX_OPEN";
      NYRejectMaxOpen++;
   }
   else if(PendingPullbackAction == action)
   {
      strict_status = "WAIT_PULLBACK";
      strict_reason = "ANTI_CHASE";
      NYStrictWaitPullback++;
   }
   else
   {
      NYRejectOther++;
   }

   string shadow_a_status = NYShadowStatus(
      ShadowA, action, confidence, news_risk, current_bar
   );
   string shadow_b_status = NYShadowStatus(
      ShadowB, action, confidence, news_risk, current_bar
   );

   if(EnableShadowMode && confidence >= ShadowA.min_confidence && news_risk != "HIGH")
      NYShadowAEligible++;
   if(EnableShadowMode && confidence >= ShadowB.min_confidence && news_risk != "HIGH")
      NYShadowBEligible++;
   if(shadow_a_status == "OPENED")
      NYShadowAOpened++;
   if(shadow_b_status == "OPENED")
      NYShadowBOpened++;

   NYLastEvent =
      action + " C" + IntegerToString(confidence) + " -> " +
      strict_status + " / " + strict_reason;

   NYAppendLog(
      utc_now,
      et_now,
      session_key,
      current_bar,
      action,
      confidence,
      technical,
      strict_status,
      strict_reason,
      shadow_a_status,
      shadow_b_status,
      news_risk,
      risk_guard,
      spread
   );
}

void NYRefreshStrictSessionPnl()
{
   if(!EnableNYSessionTracker)
      return;

   datetime utc_now = TimeGMT();
   datetime et_now = 0;
   datetime start_utc = 0;
   datetime end_utc = 0;
   string session_key = "";
   NYSessionStatus(utc_now, et_now, start_utc, end_utc, session_key);
   if(session_key != NYSessionKey)
      NYResetCounters(session_key);

   datetime effective_end_utc = utc_now < end_utc ? utc_now : end_utc;
   if(effective_end_utc <= start_utc)
   {
      NYStrictClosed = 0;
      NYStrictPnl = 0.0;
      return;
   }

   long broker_offset_seconds = (long)(TimeCurrent() - TimeGMT());
   datetime broker_start = start_utc + (datetime)broker_offset_seconds;
   datetime broker_end = effective_end_utc + (datetime)broker_offset_seconds;
   if(!HistorySelect(broker_start, broker_end))
      return;

   int closed = 0;
   double pnl = 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;
      if((ulong)HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber)
         continue;
      if(HistoryDealGetString(deal, DEAL_SYMBOL) != TradeSymbol)
         continue;

      ENUM_DEAL_TYPE type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue;

      pnl += HistoryDealGetDouble(deal, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      pnl += HistoryDealGetDouble(deal, DEAL_SWAP);
      pnl += HistoryDealGetDouble(deal, DEAL_FEE);

      ENUM_DEAL_ENTRY entry_kind =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry_kind == DEAL_ENTRY_OUT || entry_kind == DEAL_ENTRY_OUT_BY)
         closed++;
   }

   NYStrictClosed = closed;
   NYStrictPnl = pnl;
}

void NYConfigureObject(const string name)
{
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
}

void NYSetPanelBackground()
{
   string name = NYPanelPrefix + "BG";
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, NYPanelRight);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, NYPanelTop);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, NYPanelWidth);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, NYPanelHeight);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, (long)ColorToARGB(C'20,24,31', 215));
   ObjectSetInteger(0, name, OBJPROP_BORDER_COLOR, C'85,95,110');
   NYConfigureObject(name);
}

void NYSetPanelLine(
   const string id,
   const string text,
   const int y,
   const int font_size,
   const color text_color
)
{
   string name = NYPanelPrefix + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, NYPanelRight + 16);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, NYPanelTop + y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, text_color);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   NYConfigureObject(name);
}

color NYStatusColor(const string status)
{
   if(status == "OPEN") return clrLime;
   if(status == "PRE-OPEN") return clrGold;
   return clrSilver;
}

void NYTrackerDraw()
{
   if(!EnableNYSessionTracker)
      return;

   datetime utc_now = TimeGMT();
   datetime et_now = 0;
   datetime start_utc = 0;
   datetime end_utc = 0;
   string session_key = "";
   string status = NYSessionStatus(
      utc_now, et_now, start_utc, end_utc, session_key
   );
   if(session_key != NYSessionKey)
      NYResetCounters(session_key);

   NYSetPanelBackground();
   NYSetPanelLine("TITLE", "NEW YORK SESSION TRACKER", 14, NYPanelFontSize + 3, clrDeepSkyBlue);
   NYSetPanelLine(
      "STATUS",
      "Status: " + status + "  |  ET " + TimeToString(et_now, TIME_MINUTES) +
      "  |  Window " + StringFormat("%02d:%02d-%02d:%02d", NYSessionStartHourET, NYSessionStartMinuteET, NYSessionEndHourET, NYSessionEndMinuteET),
      45,
      NYPanelFontSize,
      NYStatusColor(status)
   );
   NYSetPanelLine(
      "CAND",
      "Candidates: " + IntegerToString(NYCandidates) +
      "  |  Strict opened: " + IntegerToString(NYStrictOpened) +
      "  |  Wait PB: " + IntegerToString(NYStrictWaitPullback),
      76,
      NYPanelFontSize,
      clrWhite
   );
   NYSetPanelLine(
      "REJECT",
      "Reject C: " + IntegerToString(NYRejectConfidence) +
      "  News: " + IntegerToString(NYRejectNews) +
      "  Spread: " + IntegerToString(NYRejectSpread) +
      "  Risk: " + IntegerToString(NYRejectRisk) +
      "  Other: " + IntegerToString(NYRejectMaxOpen + NYRejectOther),
      106,
      NYPanelFontSize - 1,
      clrSilver
   );
   NYSetPanelLine(
      "SHA",
      "Shadow C" + IntegerToString(ShadowA.min_confidence) +
      ": eligible " + IntegerToString(NYShadowAEligible) +
      "  |  opened " + IntegerToString(NYShadowAOpened),
      136,
      NYPanelFontSize,
      clrDeepSkyBlue
   );
   NYSetPanelLine(
      "SHB",
      "Shadow C" + IntegerToString(ShadowB.min_confidence) +
      ": eligible " + IntegerToString(NYShadowBEligible) +
      "  |  opened " + IntegerToString(NYShadowBOpened),
      166,
      NYPanelFontSize,
      clrDeepSkyBlue
   );
   NYSetPanelLine(
      "PNL",
      "Strict NY closed: " + IntegerToString(NYStrictClosed) +
      "  |  P/L: " + SignedMoney(NYStrictPnl),
      196,
      NYPanelFontSize + 1,
      NYStrictPnl > 0.0 ? clrLime : (NYStrictPnl < 0.0 ? clrTomato : clrGold)
   );
   NYSetPanelLine("LAST", "Last: " + NYLastEvent, 226, NYPanelFontSize - 1, clrWhite);
   ChartRedraw();
}

void NYTrackerOnTimer()
{
   if(!EnableNYSessionTracker)
      return;
   NYRefreshStrictSessionPnl();
   NYTrackerDraw();
}

void NYTrackerInit()
{
   if(!EnableNYSessionTracker)
      return;
   datetime utc_now = TimeGMT();
   datetime et_now = 0;
   datetime start_utc = 0;
   datetime end_utc = 0;
   string session_key = "";
   NYSessionStatus(utc_now, et_now, start_utc, end_utc, session_key);
   NYResetCounters(session_key);
   NYInitializeLog();
   NYRefreshStrictSessionPnl();
   NYTrackerDraw();
   Print(
      "MetaTraderAI NY tracker ready. ET window ",
      StringFormat("%02d:%02d-%02d:%02d", NYSessionStartHourET, NYSessionStartMinuteET, NYSessionEndHourET, NYSessionEndMinuteET)
   );
}

void NYTrackerDeinit()
{
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, NYPanelPrefix) == 0)
         ObjectDelete(0, name);
   }
}

#endif // META_TRADER_AI_NY_SESSION_TRACKER_MQH
