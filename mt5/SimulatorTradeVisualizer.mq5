#property strict
#property script_show_inputs
#property description "Visualize historical simulator trades on an MT5 chart. Read-only: no order functions."

input string SimulatorFile = "ea_simulator_trades.csv";
input int MaxTrades = 500;
input bool ClearExisting = true;
input bool ClearOnly = false;
input bool ShowEntryExitPath = true;
input bool ShowStopTarget = true;
input bool ShowLabels = true;
input int LabelFontSize = 9;

string PREFIX = "SIM_TRADE_";

void ClearObjects()
{
   for(int i = ObjectsTotal(0, -1, -1) - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i, -1, -1);
      if(StringFind(name, PREFIX) == 0)
         ObjectDelete(0, name);
   }
}

datetime ParseSimulatorTime(string value)
{
   StringReplace(value, "-", ".");
   return StringToTime(value);
}

void ConfigureObject(const string name)
{
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTED, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

void DrawArrow(
   const string name,
   const datetime when,
   const double price,
   const bool is_buy
)
{
   if(!ObjectCreate(0, name, OBJ_ARROW, 0, when, price))
      return;
   ObjectSetInteger(0, name, OBJPROP_ARROWCODE, is_buy ? 241 : 242);
   ObjectSetInteger(0, name, OBJPROP_COLOR, is_buy ? clrLime : clrTomato);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ConfigureObject(name);
}

void DrawExitMark(
   const string name,
   const datetime when,
   const double price,
   const bool win
)
{
   if(!ObjectCreate(0, name, OBJ_TEXT, 0, when, price))
      return;
   ObjectSetString(0, name, OBJPROP_TEXT, "EXIT");
   ObjectSetInteger(0, name, OBJPROP_COLOR, win ? clrLime : clrTomato);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, LabelFontSize);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_CENTER);
   ConfigureObject(name);
}

void DrawSegment(
   const string name,
   const datetime from_time,
   const double from_price,
   const datetime to_time,
   const double to_price,
   const color line_color,
   const ENUM_LINE_STYLE style,
   const int width
)
{
   datetime effective_to = to_time;
   if(effective_to <= from_time)
      effective_to = from_time + 60;

   if(!ObjectCreate(0, name, OBJ_TREND, 0, from_time, from_price, effective_to, to_price))
      return;
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_COLOR, line_color);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ConfigureObject(name);
}

void DrawTradeLabel(
   const string name,
   const datetime when,
   const double price,
   const string side,
   const string outcome,
   const double pnl_r,
   const double pnl_usd,
   const int confidence
)
{
   if(!ObjectCreate(0, name, OBJ_TEXT, 0, when, price))
      return;

   string text = StringFormat(
      "%s %s  conf=%d  %+.2fR  %+.2f USD",
      side,
      outcome,
      confidence,
      pnl_r,
      pnl_usd
   );
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, pnl_usd >= 0.0 ? clrLime : clrTomato);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, LabelFontSize);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
   ConfigureObject(name);
}

bool ReadRow(
   const int handle,
   string &signal_time,
   string &entry_time,
   string &exit_time,
   string &side,
   string &confidence,
   string &technical_score,
   string &entry_type,
   string &stop_source,
   string &entry_price,
   string &stop_price,
   string &target_price,
   string &exit_price,
   string &stop_points,
   string &spread_points,
   string &spread_to_atr,
   string &risk_money,
   string &pnl_r,
   string &pnl_usd,
   string &outcome,
   string &holding_bars,
   string &balance_after
)
{
   if(FileIsEnding(handle))
      return false;

   signal_time = FileReadString(handle);
   if(signal_time == "" && FileIsEnding(handle))
      return false;

   entry_time = FileReadString(handle);
   exit_time = FileReadString(handle);
   side = FileReadString(handle);
   confidence = FileReadString(handle);
   technical_score = FileReadString(handle);
   entry_type = FileReadString(handle);
   stop_source = FileReadString(handle);
   entry_price = FileReadString(handle);
   stop_price = FileReadString(handle);
   target_price = FileReadString(handle);
   exit_price = FileReadString(handle);
   stop_points = FileReadString(handle);
   spread_points = FileReadString(handle);
   spread_to_atr = FileReadString(handle);
   risk_money = FileReadString(handle);
   pnl_r = FileReadString(handle);
   pnl_usd = FileReadString(handle);
   outcome = FileReadString(handle);
   holding_bars = FileReadString(handle);
   balance_after = FileReadString(handle);
   return true;
}

void OnStart()
{
   if(MaxTrades < 1)
   {
      Print("SimulatorTradeVisualizer: MaxTrades must be positive.");
      return;
   }

   if(ClearExisting || ClearOnly)
      ClearObjects();
   if(ClearOnly)
   {
      ChartRedraw();
      Print("SimulatorTradeVisualizer: cleared simulator objects.");
      return;
   }

   int handle = FileOpen(SimulatorFile, FILE_READ | FILE_CSV | FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print(
         "SimulatorTradeVisualizer: cannot open MQL5/Files/",
         SimulatorFile,
         " error=",
         GetLastError()
      );
      return;
   }

   string signal_time, entry_time, exit_time, side, confidence, technical_score;
   string entry_type, stop_source, entry_price, stop_price, target_price, exit_price;
   string stop_points, spread_points, spread_to_atr, risk_money, pnl_r, pnl_usd;
   string outcome, holding_bars, balance_after;

   // Header
   if(!ReadRow(
      handle,
      signal_time, entry_time, exit_time, side, confidence, technical_score,
      entry_type, stop_source, entry_price, stop_price, target_price, exit_price,
      stop_points, spread_points, spread_to_atr, risk_money, pnl_r, pnl_usd,
      outcome, holding_bars, balance_after
   ))
   {
      FileClose(handle);
      Print("SimulatorTradeVisualizer: file is empty.");
      return;
   }

   int plotted = 0;
   int row = 0;
   while(!FileIsEnding(handle) && plotted < MaxTrades)
   {
      if(!ReadRow(
         handle,
         signal_time, entry_time, exit_time, side, confidence, technical_score,
         entry_type, stop_source, entry_price, stop_price, target_price, exit_price,
         stop_points, spread_points, spread_to_atr, risk_money, pnl_r, pnl_usd,
         outcome, holding_bars, balance_after
      ))
         break;

      row++;
      datetime entry_dt = ParseSimulatorTime(entry_time);
      datetime exit_dt = ParseSimulatorTime(exit_time);
      double entry = StringToDouble(entry_price);
      double stop = StringToDouble(stop_price);
      double target = StringToDouble(target_price);
      double exit = StringToDouble(exit_price);
      double result_r = StringToDouble(pnl_r);
      double result_usd = StringToDouble(pnl_usd);
      int conf = (int)StringToInteger(confidence);
      bool is_buy = side == "BUY";
      bool win = result_usd >= 0.0;

      if(entry_dt <= 0 || exit_dt <= 0 || entry <= 0.0 || exit <= 0.0)
         continue;

      string base = PREFIX + IntegerToString(row) + "_";
      DrawArrow(base + "ENTRY", entry_dt, entry, is_buy);
      DrawExitMark(base + "EXIT", exit_dt, exit, win);

      if(ShowEntryExitPath)
         DrawSegment(
            base + "PATH",
            entry_dt,
            entry,
            exit_dt,
            exit,
            is_buy ? clrDeepSkyBlue : clrOrange,
            STYLE_SOLID,
            1
         );

      if(ShowStopTarget)
      {
         DrawSegment(base + "SL", entry_dt, stop, exit_dt, stop, clrTomato, STYLE_DASH, 1);
         DrawSegment(base + "TP", entry_dt, target, exit_dt, target, clrLimeGreen, STYLE_DASH, 1);
      }

      if(ShowLabels)
         DrawTradeLabel(
            base + "LABEL",
            exit_dt,
            exit,
            side,
            outcome,
            result_r,
            result_usd,
            conf
         );

      plotted++;
   }

   FileClose(handle);
   ChartRedraw();
   Print("SimulatorTradeVisualizer: plotted ", plotted, " simulated trades from ", SimulatorFile);
}
