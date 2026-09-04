#property strict
#property description "Read-only M15 snapshot + H1/H4 context exporter. Contains no order functions."

input string InputSymbol = "XAUUSD_o";
input ENUM_TIMEFRAMES InputTimeframe = PERIOD_M15;
input int InputBars = 100;
input int InputIntervalSeconds = 5;
input string OutputFile = "mt5_snapshot.json";

// Export H1/H4 from the SAME EA so market structure cannot go stale just
// because a second bridge was detached from another chart.
input bool ExportHigherTimeframeContext = true;
input int ContextBars = 100;
input string ContextOutputFile = "mt5_context.json";

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
      value.year,
      value.mon,
      value.day,
      value.hour,
      value.min,
      value.sec
   );
}

datetime BrokerDayStart()
{
   MqlDateTime value;
   TimeToStruct(TimeCurrent(), value);
   value.hour = 0;
   value.min = 0;
   value.sec = 0;
   return StructToTime(value);
}

bool AccountDayRiskMetrics(double &realized_pnl, double &day_start_balance)
{
   realized_pnl = 0.0;
   day_start_balance = 0.0;

   datetime now = TimeCurrent();
   datetime day_start = BrokerDayStart();
   if(!HistorySelect(day_start, now))
   {
      Print("ReadOnlySnapshotBridge: HistorySelect failed for daily risk metrics.");
      return false;
   }

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0)
         continue;

      ENUM_DEAL_TYPE type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);
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

string RatesField(MqlRates &rates[], int copied, string field, int digits)
{
   string json = "\"" + field + "\":[";
   for(int i=copied-1; i>=0; i--)
   {
      double value = rates[i].close;
      if(field == "opens")
         value = rates[i].open;
      else if(field == "highs")
         value = rates[i].high;
      else if(field == "lows")
         value = rates[i].low;

      json += DoubleToString(value, digits);
      if(i > 0)
         json += ",";
   }
   json += "]";
   return json;
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
   // shift=1 excludes the still-forming candle on every timeframe.
   copied = CopyRates(InputSymbol, timeframe, 1, requested_bars, rates);
   return copied >= minimum_bars;
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

bool WriteTextFile(const string file_name, const string payload)
{
   int handle = FileOpen(file_name, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print(
         "ReadOnlySnapshotBridge FileOpen failed for ",
         file_name,
         ": ",
         GetLastError()
      );
      return false;
   }

   FileWriteString(handle, payload);
   FileClose(handle);
   return true;
}

bool WriteSnapshot(const MqlTick &tick, const int digits)
{
   MqlRates rates[];
   int copied = 0;
   if(!CopyCompletedRates(InputTimeframe, InputBars, rates, copied, 21))
   {
      Print("ReadOnlySnapshotBridge: not enough completed primary-timeframe bars yet.");
      return false;
   }

   double day_realized_pnl = 0.0;
   double day_start_balance = 0.0;
   bool has_day_metrics = AccountDayRiskMetrics(day_realized_pnl, day_start_balance);

   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(InputSymbol) + "\",";
   json += "\"timeframe\":\"" + EnumToString(InputTimeframe) + "\",";
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

   return WriteTextFile(OutputFile, json);
}

bool WriteHigherTimeframeContext(const int digits)
{
   if(!ExportHigherTimeframeContext)
      return true;

   MqlRates h1[];
   MqlRates h4[];
   int copied_h1 = 0;
   int copied_h4 = 0;

   if(!CopyCompletedRates(PERIOD_H1, ContextBars, h1, copied_h1, 65))
   {
      Print("ReadOnlySnapshotBridge: H1 context is not ready yet.");
      return false;
   }
   if(!CopyCompletedRates(PERIOD_H4, ContextBars, h4, copied_h4, 65))
   {
      Print("ReadOnlySnapshotBridge: H4 context is not ready yet.");
      return false;
   }

   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(InputSymbol) + "\",";
   json += "\"generated_at\":\"" + UtcIsoTimestamp() + "\",";
   json += TimeframeJson("h1", PERIOD_H1, h1, copied_h1, digits) + ",";
   json += TimeframeJson("h4", PERIOD_H4, h4, copied_h4, digits);
   json += "}";

   return WriteTextFile(ContextOutputFile, json);
}

int OnInit()
{
   if(InputBars < 21 || InputIntervalSeconds < 1)
      return INIT_PARAMETERS_INCORRECT;
   if(ExportHigherTimeframeContext && ContextBars < 65)
      return INIT_PARAMETERS_INCORRECT;

   if(!SymbolSelect(InputSymbol, true))
   {
      Print("ReadOnlySnapshotBridge: could not select symbol ", InputSymbol, ".");
      return INIT_FAILED;
   }

   EventSetTimer((int)MathMax(1, InputIntervalSeconds));
   Print(
      "ReadOnlySnapshotBridge ready: symbol=", InputSymbol,
      " snapshot=", OutputFile,
      " htf_context=", ExportHigherTimeframeContext ? ContextOutputFile : "OFF"
   );
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   MqlTick tick;
   if(!SymbolInfoTick(InputSymbol, tick))
      return;

   int digits = (int)SymbolInfoInteger(InputSymbol, SYMBOL_DIGITS);
   WriteSnapshot(tick, digits);
   WriteHigherTimeframeContext(digits);
}
