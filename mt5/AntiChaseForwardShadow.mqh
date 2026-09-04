#pragma once

// Paper-only anti-chase forward shadow helper.
// IMPORTANT: this include intentionally contains NO CTrade usage and NO order APIs.
// It is prepared on a research branch and is not wired into the live EA yet.

struct AntiChaseShadowState
{
   string name;
   bool use_anti_chase;
   double max_extension_atr;
   double pullback_zone_atr;
   int pullback_max_bars;

   bool open;
   string action;
   int confidence;
   string entry_type;
   double entry;
   double stop;
   double target;
   double risk_money;
   datetime opened_at;
   datetime last_executed_m15_bar;

   string pending_action;
   datetime pending_started_bar;

   int entries_today;
   int wins_today;
   int losses_today;
   double pnl_today;
   double balance;
   double day_start_balance;
   datetime stats_day;
};

void InitAntiChaseShadowState(
   AntiChaseShadowState &state,
   const string name,
   const bool use_anti_chase,
   const double max_extension_atr,
   const double pullback_zone_atr,
   const int pullback_max_bars,
   const double starting_balance,
   const datetime broker_day_start
)
{
   state.name = name;
   state.use_anti_chase = use_anti_chase;
   state.max_extension_atr = max_extension_atr;
   state.pullback_zone_atr = pullback_zone_atr;
   state.pullback_max_bars = pullback_max_bars;

   state.open = false;
   state.action = "";
   state.confidence = 0;
   state.entry_type = "";
   state.entry = 0.0;
   state.stop = 0.0;
   state.target = 0.0;
   state.risk_money = 0.0;
   state.opened_at = 0;
   state.last_executed_m15_bar = 0;

   state.pending_action = "";
   state.pending_started_bar = 0;

   state.entries_today = 0;
   state.wins_today = 0;
   state.losses_today = 0;
   state.pnl_today = 0.0;
   state.balance = starting_balance;
   state.day_start_balance = starting_balance;
   state.stats_day = broker_day_start;
}

void ResetAntiChasePending(AntiChaseShadowState &state)
{
   state.pending_action = "";
   state.pending_started_bar = 0;
}

bool AntiChaseShadowTimingAllows(
   AntiChaseShadowState &state,
   const string action,
   const datetime current_bar,
   const double entry,
   const double ema9,
   const double ema21,
   const double atr,
   const int bars_since_pending,
   bool &pullback_reentry
)
{
   pullback_reentry = false;

   if(!state.use_anti_chase)
      return true;
   if(atr <= 0.0)
      return false;

   double extension_atr =
      action == "BUY" ? (entry - ema21) / atr : (ema21 - entry) / atr;

   if(state.pending_action != "" && state.pending_action != action)
      ResetAntiChasePending(state);

   if(state.pending_action == "")
   {
      if(extension_atr > state.max_extension_atr)
      {
         state.pending_action = action;
         state.pending_started_bar = current_bar;
         return false;
      }
      return true;
   }

   if(bars_since_pending < 0 || bars_since_pending > state.pullback_max_bars)
   {
      ResetAntiChasePending(state);
      return false;
   }

   if(extension_atr > state.max_extension_atr)
      return false;

   bool trend_aligned = false;
   double distance_atr = 0.0;
   bool reclaimed = false;

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

   bool in_zone =
      distance_atr >= 0.0 && distance_atr <= state.pullback_zone_atr;

   if(trend_aligned && reclaimed && in_zone)
   {
      pullback_reentry = true;
      ResetAntiChasePending(state);
      return true;
   }

   return false;
}

void OpenAntiChasePaperTrade(
   AntiChaseShadowState &state,
   const string action,
   const int confidence,
   const string entry_type,
   const double entry,
   const double stop,
   const double target,
   const double risk_money,
   const datetime opened_at,
   const datetime current_m15_bar
)
{
   state.open = true;
   state.action = action;
   state.confidence = confidence;
   state.entry_type = entry_type;
   state.entry = entry;
   state.stop = stop;
   state.target = target;
   state.risk_money = risk_money;
   state.opened_at = opened_at;
   state.last_executed_m15_bar = current_m15_bar;
   state.entries_today++;
}

void CloseAntiChasePaperTrade(
   AntiChaseShadowState &state,
   const double pnl_usd,
   const bool win
)
{
   state.pnl_today += pnl_usd;
   state.balance += pnl_usd;
   if(win)
      state.wins_today++;
   else
      state.losses_today++;

   state.open = false;
   state.action = "";
   state.confidence = 0;
   state.entry_type = "";
   state.entry = 0.0;
   state.stop = 0.0;
   state.target = 0.0;
   state.risk_money = 0.0;
   state.opened_at = 0;
}
