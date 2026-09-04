#property strict
#property description "DEMO-only read-only journal for trades opened by DemoAutoTrader. Never sends orders."

input ulong MagicNumber = 26090315;
input string InputSymbol = "XAUUSD_o";
input int HistoryDays = 180;
input int RefreshSeconds = 30;
input string OutputFile = "demo_trade_journal.csv";
input bool VerboseLogging = true;

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

bool BuildJournal()
{
   datetime now = TimeCurrent();
   datetime from = now - (datetime)(MathMax(1, HistoryDays) * 86400);
   if(!HistorySelect(from, now))
   {
      Print("DemoTradeJournal: HistorySelect failed: ", GetLastError());
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
      if(magic != MagicNumber)
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

   int handle = FileOpen(OutputFile, FILE_WRITE|FILE_CSV|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("DemoTradeJournal: FileOpen failed: ", GetLastError());
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

      double epsilon = MathMax(1e-8, SymbolInfoDouble(item.symbol, SYMBOL_VOLUME_STEP) / 2.0);
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
         StringFormat("%I64u", MagicNumber)
      );
      written++;
   }

   FileClose(handle);
   if(VerboseLogging)
      Print("DemoTradeJournal: wrote ", written, " closed demo trades to ", OutputFile, ".");
   return true;
}

int OnInit()
{
   if(!IsDemoAccount())
   {
      Alert("DemoTradeJournal is DEMO-only.");
      return INIT_FAILED;
   }
   if(HistoryDays < 1 || RefreshSeconds < 5)
      return INIT_PARAMETERS_INCORRECT;

   EventSetTimer(RefreshSeconds);
   BuildJournal();
   Print(
      "DemoTradeJournal ready. magic=", MagicNumber,
      " symbol=", InputSymbol,
      " output=", OutputFile
   );
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   BuildJournal();
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}
