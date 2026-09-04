#property strict
#property description "Unified read-only bridge: M15 snapshot, H1/H4 context, daily risk metrics, and demo trade journal. Never sends orders."

input string InputSymbol = "XAUUSD_o";
input ENUM_TIMEFRAMES InputTimeframe = PERIOD_M15;
input int InputBars = 100;
input int RefreshSeconds = 5;
input string SnapshotOutputFile = "mt5_snapshot.json";

input bool ExportHigherTimeframeContext = true;
input int ContextBars = 100;
input string ContextOutputFile = "mt5_context.json";

input bool ExportDemoTradeJournal = true;
input ulong JournalMagicNumber = 26090315;
input int JournalHistoryDays = 180;
input int JournalRefreshSeconds = 30;
input string JournalOutputFile = "demo_trade_journal.csv";
input bool VerboseLogging = true;

ulong LastJournalRefreshMs = 0;

struct PositionAggregate
{
   ulong position_id;
   datetime opened_at;
   datetime closed_at;
   string symbol;
   string side;
   double entry_volume;
   double exit_volume;
   double entry_price_value;
   double exit_price_value;
   double initial_sl;
   double initial_tp;
   double net_pnl;
};

bool IsDemoAccount()
{
   ENUM_ACCOUNT_TRADE_MODE mode =
      (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   return mode == ACCOUNT_TRADE_MODE_DEMO;
}

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

string BrokerTimestamp(const datetime value)
{
   if(value <= 0)
      return "";
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return StringFormat(
      "%04d-%02d-%02dT%02d:%02d:%02d",
      parts.year,
      parts.mon,
      parts.day,
      parts.hour,
      parts.min,
      parts.sec
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
      Print("MetaTraderAIBridge: HistorySelect failed for daily risk metrics.");
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
   for(int i = copied - 1; i >= 0; i--)
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
      Print("MetaTraderAIBridge FileOpen failed for ", file_name, ": ", GetLastError());
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
      Print("MetaTraderAIBridge: not enough completed primary-timeframe bars yet.");
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

   return WriteTextFile(SnapshotOutputFile, json);
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
      Print("MetaTraderAIBridge: H1 context is not ready yet.");
      return false;
   }
   if(!CopyCompletedRates(PERIOD_H4, ContextBars, h4, copied_h4, 65))
   {
      Print("MetaTraderAIBridge: H4 context is not ready yet.");
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

int FindPosition(PositionAggregate &items[], const ulong position_id)
{
   for(int i = 0; i < ArraySize(items); i++)
   {
      if(items[i].position_id == position_id)
         return i;
   }
   return -1;
}

int FindOrAddPosition(PositionAggregate &items[], const ulong position_id)
{
   int existing = FindPosition(items, position_id);
   if(existing >= 0)
      return existing;

   int index = ArraySize(items);
   ArrayResize(items, index + 1);
   items[index].position_id = position_id;
   items[index].opened_at = 0;
   items[index].closed_at = 0;
   items[index].symbol = "";
   items[index].side = "";
   items[index].entry_volume = 0.0;
   items[index].exit_volume = 0.0;
   items[index].entry_price_value = 0.0;
   items[index].exit_price_value = 0.0;
   items[index].initial_sl = 0.0;
   items[index].initial_tp = 0.0;
   items[index].net_pnl = 0.0;
   return index;
}

bool InitialRiskMoney(
   PositionAggregate &item,
   const double entry_price,
   double &risk_money
)
{
   risk_money = 0.0;
   if(item.initial_sl <= 0.0 || item.entry_volume <= 0.0 || entry_price <= 0.0)
      return false;

   if(!SymbolSelect(item.symbol, true))
      return false;

   ENUM_ORDER_TYPE order_type = item.side == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double stop_profit = 0.0;
   if(!OrderCalcProfit(
      order_type,
      item.symbol,
      item.entry_volume,
      entry_price,
      item.initial_sl,
      stop_profit
   ))
      return false;

   risk_money = MathAbs(stop_profit);
   return risk_money > 0.0;
}

bool BuildDemoTradeJournal()
{
   if(!ExportDemoTradeJournal)
      return true;
   if(!IsDemoAccount())
   {
      if(VerboseLogging)
         Print("MetaTraderAIBridge: demo journal skipped because account is not DEMO.");
      return true;
   }

   datetime now = TimeCurrent();
   datetime from = now - (datetime)(MathMax(1, JournalHistoryDays) * 86400);
   if(!HistorySelect(from, now))
   {
      Print("MetaTraderAIBridge: HistorySelect failed for demo journal: ", GetLastError());
      return false;
   }

   PositionAggregate items[];
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      ulong magic = (ulong)HistoryDealGetInteger(deal, DEAL_MAGIC);
      if(magic != JournalMagicNumber)
         continue;

      string symbol = HistoryDealGetString(deal, DEAL_SYMBOL);
      if(InputSymbol != "" && symbol != InputSymbol)
         continue;

      ulong position_id = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      if(position_id == 0)
         continue;

      ENUM_DEAL_ENTRY entry_kind =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
      ENUM_DEAL_TYPE deal_type =
         (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE);
      if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL)
         continue;

      int index = FindOrAddPosition(items, position_id);
      double volume = HistoryDealGetDouble(deal, DEAL_VOLUME);
      double price = HistoryDealGetDouble(deal, DEAL_PRICE);
      datetime deal_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);

      items[index].symbol = symbol;
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_PROFIT);
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_SWAP);
      items[index].net_pnl += HistoryDealGetDouble(deal, DEAL_FEE);

      if(entry_kind == DEAL_ENTRY_IN)
      {
         items[index].entry_volume += volume;
         items[index].entry_price_value += price * volume;
         if(items[index].opened_at == 0 || deal_time < items[index].opened_at)
            items[index].opened_at = deal_time;
         if(items[index].side == "")
            items[index].side = deal_type == DEAL_TYPE_BUY ? "BUY" : "SELL";

         if(items[index].initial_sl <= 0.0)
         {
            ulong order_ticket = (ulong)HistoryDealGetInteger(deal, DEAL_ORDER);
            if(order_ticket > 0)
            {
               items[index].initial_sl = HistoryOrderGetDouble(order_ticket, ORDER_SL);
               items[index].initial_tp = HistoryOrderGetDouble(order_ticket, ORDER_TP);
            }
         }
      }
      else if(entry_kind == DEAL_ENTRY_OUT || entry_kind == DEAL_ENTRY_OUT_BY)
      {
         items[index].exit_volume += volume;
         items[index].exit_price_value += price * volume;
         if(deal_time > items[index].closed_at)
            items[index].closed_at = deal_time;
      }
   }

   int handle = FileOpen(JournalOutputFile, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("MetaTraderAIBridge: journal FileOpen failed: ", GetLastError());
      return false;
   }

   FileWrite(
      handle,
      "position_id",
      "opened_at_broker",
      "closed_at_broker",
      "symbol",
      "side",
      "volume",
      "entry_price",
      "exit_price",
      "initial_sl",
      "initial_tp",
      "net_pnl",
      "planned_risk_money",
      "pnl_r",
      "outcome",
      "magic"
   );

   int written = 0;
   for(int i = 0; i < ArraySize(items); i++)
   {
      PositionAggregate item = items[i];
      if(item.entry_volume <= 0.0)
         continue;

      double epsilon = MathMax(
         1e-8,
         SymbolInfoDouble(item.symbol, SYMBOL_VOLUME_STEP) / 2.0
      );
      if(item.closed_at <= 0 || item.exit_volume + epsilon < item.entry_volume)
         continue;

      double entry_price = item.entry_price_value / item.entry_volume;
      double exit_price = item.exit_volume > 0.0
         ? item.exit_price_value / item.exit_volume
         : 0.0;
      double risk_money = 0.0;
      bool has_risk = InitialRiskMoney(item, entry_price, risk_money);
      string pnl_r = has_risk
         ? DoubleToString(item.net_pnl / risk_money, 6)
         : "";
      string outcome = item.net_pnl > 1e-8
         ? "WIN"
         : item.net_pnl < -1e-8
         ? "LOSS"
         : "FLAT";

      int digits = (int)SymbolInfoInteger(item.symbol, SYMBOL_DIGITS);
      FileWrite(
         handle,
         StringFormat("%I64u", item.position_id),
         BrokerTimestamp(item.opened_at),
         BrokerTimestamp(item.closed_at),
         item.symbol,
         item.side,
         DoubleToString(item.entry_volume, 4),
         DoubleToString(entry_price, digits),
         DoubleToString(exit_price, digits),
         item.initial_sl > 0.0 ? DoubleToString(item.initial_sl, digits) : "",
         item.initial_tp > 0.0 ? DoubleToString(item.initial_tp, digits) : "",
         DoubleToString(item.net_pnl, 2),
         has_risk ? DoubleToString(risk_money, 2) : "",
         pnl_r,
         outcome,
         StringFormat("%I64u", JournalMagicNumber)
      );
      written++;
   }

   FileClose(handle);
   if(VerboseLogging)
      Print("MetaTraderAIBridge: wrote ", written, " closed demo trades to ", JournalOutputFile, ".");
   return true;
}

void RefreshAll()
{
   MqlTick tick;
   if(!SymbolInfoTick(InputSymbol, tick))
   {
      Print("MetaTraderAIBridge: SymbolInfoTick failed for ", InputSymbol, ".");
      return;
   }

   int digits = (int)SymbolInfoInteger(InputSymbol, SYMBOL_DIGITS);
   WriteSnapshot(tick, digits);
   WriteHigherTimeframeContext(digits);

   ulong now_ms = GetTickCount64();
   if(
      ExportDemoTradeJournal
      && (LastJournalRefreshMs == 0
          || now_ms - LastJournalRefreshMs >= (ulong)JournalRefreshSeconds * 1000)
   )
   {
      BuildDemoTradeJournal();
      LastJournalRefreshMs = now_ms;
   }
}

int OnInit()
{
   if(InputBars < 21 || RefreshSeconds < 1)
      return INIT_PARAMETERS_INCORRECT;
   if(ExportHigherTimeframeContext && ContextBars < 65)
      return INIT_PARAMETERS_INCORRECT;
   if(
      ExportDemoTradeJournal
      && (JournalHistoryDays < 1 || JournalRefreshSeconds < RefreshSeconds)
   )
      return INIT_PARAMETERS_INCORRECT;

   if(!SymbolSelect(InputSymbol, true))
   {
      Print("MetaTraderAIBridge: could not select symbol ", InputSymbol, ".");
      return INIT_FAILED;
   }

   EventSetTimer((int)MathMax(1, RefreshSeconds));
   RefreshAll();
   Print(
      "MetaTraderAIBridge ready: symbol=", InputSymbol,
      " snapshot=", SnapshotOutputFile,
      " htf=", ExportHigherTimeframeContext ? ContextOutputFile : "OFF",
      " journal=", ExportDemoTradeJournal ? JournalOutputFile : "OFF"
   );
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   RefreshAll();
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}
