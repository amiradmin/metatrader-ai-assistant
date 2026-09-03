#property strict
#property description "Read-only H1/H4 context exporter. Contains no order functions."

input string InputSymbol = "XAUUSD_o";
input int InputBars = 100;
input int InputIntervalSeconds = 15;
input string OutputFile = "mt5_context.json";

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
   MqlRates &rates[],
   int &copied
)
{
   ArraySetAsSeries(rates, true);
   // shift=1 means the still-forming candle is never exported.
   copied = CopyRates(InputSymbol, timeframe, 1, InputBars, rates);
   return copied >= 65;
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

int OnInit()
{
   if(InputBars < 65 || InputIntervalSeconds < 1)
      return INIT_PARAMETERS_INCORRECT;

   if(!SymbolSelect(InputSymbol, true))
      return INIT_FAILED;

   EventSetTimer((int)MathMax(1, InputIntervalSeconds));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   MqlRates h1[];
   MqlRates h4[];
   int copied_h1 = 0;
   int copied_h4 = 0;

   if(!CopyCompletedRates(PERIOD_H1, h1, copied_h1))
      return;
   if(!CopyCompletedRates(PERIOD_H4, h4, copied_h4))
      return;

   int handle = FileOpen(OutputFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("ReadOnlyMarketContextBridge FileOpen failed: ", GetLastError());
      return;
   }

   int digits = (int)SymbolInfoInteger(InputSymbol, SYMBOL_DIGITS);
   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(InputSymbol) + "\",";
   json += "\"generated_at\":\"" + UtcIsoTimestamp() + "\",";
   json += TimeframeJson("h1", PERIOD_H1, h1, copied_h1, digits) + ",";
   json += TimeframeJson("h4", PERIOD_H4, h4, copied_h4, digits);
   json += "}";

   FileWriteString(handle, json);
   FileClose(handle);
}
