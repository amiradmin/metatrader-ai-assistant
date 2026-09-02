#property strict
#property script_show_inputs
#property description "Read-only historical OHLCV CSV exporter. Contains no order functions."

input string InputSymbol = "XAUUSD_o";
input ENUM_TIMEFRAMES InputTimeframe = PERIOD_M15;
input int InputBars = 50000;
input bool IncludeCurrentBar = false;
input string OutputFile = "xauusd_m15_history.csv";

string FormatBrokerTime(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return StringFormat(
      "%04d-%02d-%02d %02d:%02d:%02d",
      parts.year,
      parts.mon,
      parts.day,
      parts.hour,
      parts.min,
      parts.sec
   );
}

void OnStart()
{
   if(InputBars < 100)
   {
      Print("InputBars must be at least 100.");
      return;
   }

   if(!SymbolSelect(InputSymbol, true))
   {
      PrintFormat("Could not select symbol %s. Check the broker symbol name.", InputSymbol);
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, false);

   const int start_position = IncludeCurrentBar ? 0 : 1;
   ResetLastError();
   const int copied = CopyRates(
      InputSymbol,
      InputTimeframe,
      start_position,
      InputBars,
      rates
   );

   if(copied <= 0)
   {
      PrintFormat(
         "CopyRates failed for %s/%s. Error=%d",
         InputSymbol,
         EnumToString(InputTimeframe),
         GetLastError()
      );
      return;
   }

   ResetLastError();
   const int handle = FileOpen(
      OutputFile,
      FILE_WRITE | FILE_CSV | FILE_ANSI,
      ','
   );

   if(handle == INVALID_HANDLE)
   {
      PrintFormat("Could not open %s. Error=%d", OutputFile, GetLastError());
      return;
   }

   FileWrite(
      handle,
      "time",
      "open",
      "high",
      "low",
      "close",
      "tick_volume",
      "spread",
      "real_volume"
   );

   const int digits = (int)SymbolInfoInteger(InputSymbol, SYMBOL_DIGITS);

   for(int i = 0; i < copied; i++)
   {
      FileWrite(
         handle,
         FormatBrokerTime(rates[i].time),
         DoubleToString(rates[i].open, digits),
         DoubleToString(rates[i].high, digits),
         DoubleToString(rates[i].low, digits),
         DoubleToString(rates[i].close, digits),
         (long)rates[i].tick_volume,
         rates[i].spread,
         (long)rates[i].real_volume
      );
   }

   FileFlush(handle);
   FileClose(handle);

   PrintFormat(
      "Exported %d closed bars for %s/%s to MQL5/Files/%s. Times are broker-server time; spread is in points.",
      copied,
      InputSymbol,
      EnumToString(InputTimeframe),
      OutputFile
   );
}
