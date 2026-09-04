// MetaTraderAI wrapper: preserves the tested trading core and adds the
// read-only New York session tracker without changing the strict order path.

#define OnInit MetaTraderAICore_OnInit
#define OnTick MetaTraderAICore_OnTick
#define OnTimer MetaTraderAICore_OnTimer
#define OnDeinit MetaTraderAICore_OnDeinit
#include "MetaTraderAI_Core.mqh"
#undef OnInit
#undef OnTick
#undef OnTimer
#undef OnDeinit

#include "NYSessionTracker.mqh"

ulong NYWrapperLastObservedSignalMs = 0;

int OnInit()
{
   int result = MetaTraderAICore_OnInit();
   if(result != INIT_SUCCEEDED)
      return result;

   NYTrackerInit();
   NYWrapperLastObservedSignalMs = LastSignalMs;
   if(LastPanelStatus == "CONNECTED")
      NYTrackerOnSignal(LastApiPayload);
   NYTrackerOnTimer();
   return INIT_SUCCEEDED;
}

void OnTick()
{
   MetaTraderAICore_OnTick();
}

void OnTimer()
{
   MetaTraderAICore_OnTimer();

   // Only inspect a hint when the core actually performed a new signal refresh.
   // This prevents an old API response from being counted on a new M15 bar.
   if(LastSignalMs != NYWrapperLastObservedSignalMs)
   {
      NYWrapperLastObservedSignalMs = LastSignalMs;
      if(LastPanelStatus == "CONNECTED")
         NYTrackerOnSignal(LastApiPayload);
   }

   NYTrackerOnTimer();
}

void OnDeinit(const int reason)
{
   NYTrackerDeinit();
   MetaTraderAICore_OnDeinit(reason);
}
