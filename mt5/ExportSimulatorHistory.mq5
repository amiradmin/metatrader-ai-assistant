#property strict
#property script_show_inputs
#property description "One-click read-only exporter for simulator M1 + M15 history. No order functions."

input string InputSymbol = "XAUUSD_o";
// 300k M1 bars is enough for roughly 180+ completed gold trading days when the
// broker makes that much history available. The exporter will warn if MT5 can
// only supply a partial history window.
input int M1Bars = 300000;
input int M15Bars = 50000;

string FormatBrokerTime(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return StringFormat(
      "%04d-%02d-%02d %02d:%02d:%02d",
      parts.year, parts.mon, parts.day,
      parts.hour, parts.min, parts.sec
   );
}

bool ExportTimeframe(
   const ENUM_TIMEFRAMES timeframe,
   const int requested_bars,
   const string output_file
)
{
   if(requested_bars < 100)
   {
      Print("ExportSimulatorHistory: requested bars must be at least 100 for ", EnumToString(timeframe));
      return false;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   int copied = CopyRates(InputSymbol, timeframe, 1, requested_bars, rates);
   int copy_error = GetLastError();
   if(copied <= 0)
   {
      Print(
         "ExportSimulatorHistory: CopyRates failed for ", InputSymbol, "/",
         EnumToString(timeframe), " copied=", copied, " error=", copy_error
      );
      return false;
   }

   int handle = FileOpen(output_file, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("ExportSimulatorHistory: FileOpen failed for ", output_file, " error=", GetLastError());
      return false;
   }

   FileWrite(handle, "time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume");
   int digits = (int)SymbolInfoInteger(InputSymbol, SYMBOL_DIGITS);
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

   if(copied < requested_bars)
   {
      Print(
         "ExportSimulatorHistory: WARNING partial history for ", EnumToString(timeframe),
         " requested=", requested_bars, " copied=", copied,
         ". MT5/broker has not supplied the full requested window yet."
      );
   }

   Print(
      "ExportSimulatorHistory: SUCCESS ", EnumToString(timeframe),
      " bars=", copied, " requested=", requested_bars,
      " file=MQL5/Files/", output_file,
      " range=", FormatBrokerTime(rates[0].time),
      " -> ", FormatBrokerTime(rates[copied - 1].time)
   );
   return true;
}

void OnStart()
{
   if(!SymbolSelect(InputSymbol, true))
   {
      Print("ExportSimulatorHistory: could not select ", InputSymbol, " error=", GetLastError());
      return;
   }

   bool m1_ok = ExportTimeframe(PERIOD_M1, M1Bars, "xauusd_m1_history.csv");
   bool m15_ok = ExportTimeframe(PERIOD_M15, M15Bars, "xauusd_m15_history.csv");

   if(m1_ok && m15_ok)
      Print("ExportSimulatorHistory: READY. M1 + M15 simulator history refreshed.");
   else
      Print("ExportSimulatorHistory: INCOMPLETE. Check Experts log for the failed timeframe.");
}
