#property strict
#property description "Read-only M15-first snapshot exporter. Contains no order functions."

input string InputSymbol = "EURUSD";
input ENUM_TIMEFRAMES InputTimeframe = PERIOD_M15;
input int InputBars = 100;
input int InputIntervalSeconds = 5;
input string OutputFile = "mt5_snapshot.json";

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

int OnInit()
{
   if(InputBars < 21)
      return INIT_PARAMETERS_INCORRECT;
   SymbolSelect(InputSymbol, true);
   EventSetTimer((int)MathMax(1, InputIntervalSeconds));
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

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(InputSymbol, InputTimeframe, 0, InputBars, rates);
   if(copied < 21)
      return;

   int handle = FileOpen(OutputFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;

   int digits = (int)SymbolInfoInteger(InputSymbol, SYMBOL_DIGITS);
   string json = "{";
   json += "\"symbol\":\"" + EscapeJson(InputSymbol) + "\",";
   json += "\"timeframe\":\"" + EnumToString(InputTimeframe) + "\",";
   json += "\"generated_at\":\"" + UtcIsoTimestamp() + "\",";
   json += "\"bid\":" + DoubleToString(tick.bid, digits) + ",";
   json += "\"ask\":" + DoubleToString(tick.ask, digits) + ",";
   json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   json += "\"positions_total\":" + IntegerToString(PositionsTotal()) + ",";
   json += RatesField(rates, copied, "opens", digits) + ",";
   json += RatesField(rates, copied, "highs", digits) + ",";
   json += RatesField(rates, copied, "lows", digits) + ",";
   json += RatesField(rates, copied, "closes", digits);
   json += "}";

   FileWriteString(handle, json);
   FileClose(handle);
}
