#property strict
#property description "Manual-confirm M15 trader. Never sends an order without a chart-button click."

#include <Trade/Trade.mqh>

input string ApiUrl = "http://127.0.0.1:8000/hint";
input int RefreshSeconds = 15;
input int RequestTimeoutMs = 45000;
input int TradesPerConfirm = 1;
input int MaxOpenTrades = 3;
input double LotSize = 0.01;
input int MinConfidence = 75;
input int StopLossPoints = 300;
input int TakeProfitPoints = 600;
input int MaxSpreadPoints = 50;
input ulong MagicNumber = 26090316;
input int SlippagePoints = 20;

CTrade Trade;
string Prefix = "ManualConfirmTrader_";
string PendingAction = "WAIT";
int PendingConfidence = 0;
string PendingNewsRisk = "UNKNOWN";
string PendingSymbol = "";
datetime PendingBar = 0;

string JsonValue(const string json, const string key)
{
   string needle = "\"" + key + "\"";
   int position = StringFind(json, needle);
   if(position < 0)
      return "";

   position = StringFind(json, ":", position + StringLen(needle));
   if(position < 0)
      return "";
   position++;

   int length = StringLen(json);
   while(position < length)
   {
      ushort character = StringGetCharacter(json, position);
      if(character != ' ' && character != '\t')
         break;
      position++;
   }

   if(position < length && StringGetCharacter(json, position) == '"')
   {
      int ending = StringFind(json, "\"", position + 1);
      if(ending < 0)
         return "";
      return StringSubstr(json, position + 1, ending - position - 1);
   }

   int comma = StringFind(json, ",", position);
   int brace = StringFind(json, "}", position);
   int ending = comma;
   if(ending < 0 || (brace >= 0 && brace < ending))
      ending = brace;
   if(ending < 0)
      ending = length;
   return StringSubstr(json, position, ending - position);
}

bool FetchHint(string &action, int &confidence, string &symbol, string &news_risk)
{
   char request_data[];
   char response_data[];
   string response_headers;
   ArrayResize(request_data, 0);

   ResetLastError();
   int status_code = WebRequest(
      "GET",
      ApiUrl,
      "",
      RequestTimeoutMs,
      request_data,
      response_data,
      response_headers
   );

   if(status_code == -1)
   {
      Print("ManualConfirmTrader WebRequest failed: ", GetLastError());
      return false;
   }

   string response = CharArrayToString(response_data, 0, -1, CP_UTF8);
   if(status_code != 200)
   {
      Print("ManualConfirmTrader API HTTP ", status_code, ": ", response);
      return false;
   }

   action = JsonValue(response, "action");
   symbol = JsonValue(response, "symbol");
   news_risk = JsonValue(response, "news_risk");
   confidence = (int)StringToInteger(JsonValue(response, "confidence"));
   return action != "" && symbol != "";
}

double NormalizeVolume(double requested)
{
   double minimum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maximum = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double value = MathMax(minimum, MathMin(maximum, requested));
   if(step > 0.0)
      value = MathFloor(value / step + 0.5) * step;

   int volume_digits = 2;
   if(step >= 1.0)
      volume_digits = 0;
   else if(step >= 0.1)
      volume_digits = 1;
   else if(step >= 0.01)
      volume_digits = 2;
   else
      volume_digits = 3;

   return NormalizeDouble(value, volume_digits);
}

int ManagedOpenPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         count++;
   }
   return count;
}

bool SpreadIsAcceptable()
{
   if(MaxSpreadPoints <= 0)
      return true;
   return SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) <= MaxSpreadPoints;
}

void SetLabel(const string id, const string text, const int x, const int y, const int font_size, const color text_color)
{
   string name = Prefix + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, text_color);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void SetButton(const string id, const string text, const int x, const int y, const int width, const int height, const color background, const bool enabled)
{
   string name = Prefix + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, width);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, height);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, enabled ? background : clrDimGray);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 12);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void RenderPanel()
{
   string account_mode = "REAL";
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_DEMO)
      account_mode = "DEMO";
   else if(mode == ACCOUNT_TRADE_MODE_CONTEST)
      account_mode = "CONTEST";

   bool buy_enabled = PendingAction == "BUY" && PendingConfidence >= MinConfidence && PendingNewsRisk != "HIGH";
   bool sell_enabled = PendingAction == "SELL" && PendingConfidence >= MinConfidence && PendingNewsRisk != "HIGH";

   SetLabel("Title", "M15 AI MANUAL CONFIRM  |  " + account_mode, 20, 20, 14, clrWhite);
   SetLabel("Signal", "Signal: " + PendingAction + "   Confidence: " + IntegerToString(PendingConfidence) + "/100", 20, 50, 13, clrWhite);
   SetLabel("Risk", "News: " + PendingNewsRisk + "   Trades/confirm: " + IntegerToString(TradesPerConfirm) + "   Lot: " + DoubleToString(LotSize, 2), 20, 78, 11, clrWhite);
   SetLabel("Rule", "No order is sent until you click the matching CONFIRM button.", 20, 106, 10, clrSilver);

   SetButton("Buy", "CONFIRM BUY", 20, 138, 150, 34, clrGreen, buy_enabled);
   SetButton("Sell", "CONFIRM SELL", 180, 138, 150, 34, clrFireBrick, sell_enabled);
   SetButton("Cancel", "CANCEL", 340, 138, 100, 34, clrDarkSlateGray, true);
   ChartRedraw();
}

void RefreshPendingSignal()
{
   string action;
   string symbol;
   string news_risk;
   int confidence = 0;

   if(!FetchHint(action, confidence, symbol, news_risk))
   {
      PendingAction = "WAIT";
      PendingConfidence = 0;
      PendingNewsRisk = "UNKNOWN";
      PendingSymbol = "";
      RenderPanel();
      return;
   }

   PendingAction = action;
   PendingConfidence = confidence;
   PendingNewsRisk = news_risk;
   PendingSymbol = symbol;
   PendingBar = iTime(_Symbol, PERIOD_M15, 0);
   RenderPanel();
}

bool OpenManagedTrade(const string action)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double volume = NormalizeVolume(LotSize);
   double sl = 0.0;
   double tp = 0.0;

   if(action == "BUY")
   {
      if(StopLossPoints > 0)
         sl = NormalizeDouble(tick.ask - StopLossPoints * point, digits);
      if(TakeProfitPoints > 0)
         tp = NormalizeDouble(tick.ask + TakeProfitPoints * point, digits);
      return Trade.Buy(volume, _Symbol, 0.0, sl, tp, "M15 AI MANUAL");
   }

   if(action == "SELL")
   {
      if(StopLossPoints > 0)
         sl = NormalizeDouble(tick.bid + StopLossPoints * point, digits);
      if(TakeProfitPoints > 0)
         tp = NormalizeDouble(tick.bid - TakeProfitPoints * point, digits);
      return Trade.Sell(volume, _Symbol, 0.0, sl, tp, "M15 AI MANUAL");
   }

   return false;
}

void ConfirmAndExecute(const string requested_action)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Alert("Trading is disabled in MT5. No order was sent.");
      return;
   }

   // Re-fetch immediately at click time so a stale screen cannot execute an old signal.
   string action;
   string symbol;
   string news_risk;
   int confidence = 0;
   if(!FetchHint(action, confidence, symbol, news_risk))
   {
      Alert("Could not refresh the API signal. No order was sent.");
      return;
   }

   datetime current_bar = iTime(_Symbol, PERIOD_M15, 0);
   if(symbol != _Symbol || action != requested_action || confidence < MinConfidence || news_risk == "HIGH" || current_bar != PendingBar)
   {
      Alert("Signal changed or is no longer eligible. No order was sent.");
      RefreshPendingSignal();
      return;
   }

   if(!SpreadIsAcceptable())
   {
      Alert("Spread is above MaxSpreadPoints. No order was sent.");
      return;
   }

   int capacity = MaxOpenTrades - ManagedOpenPositions();
   if(capacity <= 0)
   {
      Alert("MaxOpenTrades reached. No order was sent.");
      return;
   }

   int to_open = MathMin(MathMax(1, TradesPerConfirm), capacity);
   int opened = 0;

   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);

   for(int i = 0; i < to_open; i++)
   {
      if(OpenManagedTrade(requested_action))
         opened++;
      else
      {
         Print("ManualConfirmTrader order failed. retcode=", Trade.ResultRetcode(), " ", Trade.ResultRetcodeDescription());
         break;
      }
   }

   if(opened > 0)
      Alert("Confirmed: opened ", opened, " ", requested_action, " trade(s). Confidence=", confidence);

   RefreshPendingSignal();
}

void CancelPending()
{
   PendingAction = "WAIT";
   PendingConfidence = 0;
   PendingNewsRisk = "UNKNOWN";
   PendingSymbol = "";
   PendingBar = 0;
   RenderPanel();
}

void DeletePanel()
{
   ObjectDelete(0, Prefix + "Title");
   ObjectDelete(0, Prefix + "Signal");
   ObjectDelete(0, Prefix + "Risk");
   ObjectDelete(0, Prefix + "Rule");
   ObjectDelete(0, Prefix + "Buy");
   ObjectDelete(0, Prefix + "Sell");
   ObjectDelete(0, Prefix + "Cancel");
}

int OnInit()
{
   if(RefreshSeconds < 5 || RequestTimeoutMs < 1000)
      return INIT_PARAMETERS_INCORRECT;
   if(TradesPerConfirm < 1 || MaxOpenTrades < 1 || LotSize <= 0.0)
      return INIT_PARAMETERS_INCORRECT;
   if(MinConfidence < 0 || MinConfidence > 100)
      return INIT_PARAMETERS_INCORRECT;

   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   EventSetTimer(RefreshSeconds);
   RenderPanel();
   RefreshPendingSignal();
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   RefreshPendingSignal();
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK)
      return;

   if(sparam == Prefix + "Buy")
   {
      ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
      if(PendingAction == "BUY" && PendingConfidence >= MinConfidence && PendingNewsRisk != "HIGH")
         ConfirmAndExecute("BUY");
   }
   else if(sparam == Prefix + "Sell")
   {
      ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
      if(PendingAction == "SELL" && PendingConfidence >= MinConfidence && PendingNewsRisk != "HIGH")
         ConfirmAndExecute("SELL");
   }
   else if(sparam == Prefix + "Cancel")
   {
      ObjectSetInteger(0, sparam, OBJPROP_STATE, false);
      CancelPending();
   }
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DeletePanel();
   ChartRedraw();
}
