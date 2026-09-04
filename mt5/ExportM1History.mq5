#property strict
#property script_show_inputs
#property description "One-click XAUUSD_o M1 history exporter for the simulator. Read-only; no order functions."

input int InputBars = 50000;
input string OutputFile = "xauusd_m1_history.csv";

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
   const string symbol = "XAUUSD_o";
   const ENUM_TIMEFRAMES timeframe = PERIOD_M1;

   if(InputBars < 100)
   {
      Print("ExportM1History: InputBars must be at least 100.");
      return;
   }

   if(!SymbolSelect(symbol, true))
   {
      Print("ExportM1History: could not select ", symbol, ". Error=", GetLastError());
      return;
   }

   Print(
      "ExportM1History: requesting up to ", InputBars,
      " closed M1 bars for ", symbol, "."
   );

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   int copied = CopyRates(symbol, timeframe, 1, InputBars, rates);
   int copy_error = GetLastError();

   if(copied <= 0)
   {
      Print(
         "ExportM1History: CopyRates failed. copied=", copied,
         " error=", copy_error,
         ". Open an XAUUSD_o M1 chart, scroll/load history, then run the script again."
      );
      return;
   }

   int handle = FileOpen(OutputFile, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print(
         "ExportM1History: could not open MQL5/Files/", OutputFile,
         ". Error=", GetLastError()
      );
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

   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
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

   Print(
      "ExportM1History: SUCCESS. Exported ", copied,
      " closed bars to MQL5/Files/", OutputFile,
      ". Range=", FormatBrokerTime(rates[0].time),
      " -> ", FormatBrokerTime(rates[copied - 1].time)
   );
}
