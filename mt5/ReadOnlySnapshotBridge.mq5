#property strict
#property description "Read-only snapshot exporter. Contains no order functions."

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

int OnInit()
{
   if(InputBars < 20)
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

   double closes[];
   ArraySetAsSeries(closes, true);
   int copied = CopyClose(InputSymbol, InputTimeframe, 0, InputBars, closes);
   if(copied < 20)
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
   json += "\"closes\":[";
   for(int i=copied-1; i>=0; i--)
   {
      json += DoubleToString(closes[i], digits);
      if(i > 0)
         json += ",";
   }
   json += "]}";

   FileWriteString(handle, json);
   FileClose(handle);
}
