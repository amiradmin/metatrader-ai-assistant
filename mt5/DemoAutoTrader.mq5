#property strict
#property description "DEMO-ONLY M15 auto trader. Hard-blocks real accounts."

#include <Trade/Trade.mqh>

input string ApiUrl = "http://127.0.0.1:8000/hint";
input int RefreshSeconds = 15;
input int RequestTimeoutMs = 45000;
input int TradesPerSignal = 1;
input int MaxOpenTrades = 3;
input double LotSize = 0.01;
input int MinConfidence = 75;
input int StopLossPoints = 300;
input int TakeProfitPoints = 600;
input int MaxSpreadPoints = 50;
input ulong MagicNumber = 26090315;
input int SlippagePoints = 20;
input bool VerboseLogging = true;

CTrade Trade;
datetime LastExecutedM15Bar = 0;

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

bool IsDemoAccount()
{
   ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
   return mode == ACCOUNT_TRADE_MODE_DEMO;
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

      string symbol = PositionGetString(POSITION_SYMBOL);
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(symbol == _Symbol && (ulong)magic == MagicNumber)
         count++;
   }
   return count;
}

long CurrentSpreadPoints()
{
   return SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
}

bool SpreadIsAcceptable()
{
   if(MaxSpreadPoints <= 0)
      return true;
   return CurrentSpreadPoints() <= MaxSpreadPoints;
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
      Print("DemoAutoTrader WebRequest failed: ", GetLastError());
      return false;
   }

   string response = CharArrayToString(response_data, 0, -1, CP_UTF8);
   if(status_code != 200)
   {
      Print("DemoAutoTrader API HTTP ", status_code, ": ", response);
      return false;
   }

   action = JsonValue(response, "action");
   symbol = JsonValue(response, "symbol");
   news_risk = JsonValue(response, "news_risk");
   string confidence_text = JsonValue(response, "confidence");
   confidence = (int)StringToInteger(confidence_text);

   return action != "" && symbol != "";
}

bool TradeResultAccepted()
{
   uint retcode = Trade.ResultRetcode();
   return (
      retcode == TRADE_RETCODE_DONE
      || retcode == TRADE_RETCODE_DONE_PARTIAL
      || retcode == TRADE_RETCODE_PLACED
   );
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
   bool request_ok = false;

   if(action == "BUY")
   {
      if(StopLossPoints > 0)
         sl = NormalizeDouble(tick.ask - StopLossPoints * point, digits);
      if(TakeProfitPoints > 0)
         tp = NormalizeDouble(tick.ask + TakeProfitPoints * point, digits);
      request_ok = Trade.Buy(volume, _Symbol, 0.0, sl, tp, "M15 AI DEMO");
   }
   else if(action == "SELL")
   {
      if(StopLossPoints > 0)
         sl = NormalizeDouble(tick.bid + StopLossPoints * point, digits);
      if(TakeProfitPoints > 0)
         tp = NormalizeDouble(tick.bid - TakeProfitPoints * point, digits);
      request_ok = Trade.Sell(volume, _Symbol, 0.0, sl, tp, "M15 AI DEMO");
   }
   else
      return false;

   if(!request_ok || !TradeResultAccepted())
      return false;

   if(VerboseLogging)
   {
      Print(
         "DemoAutoTrader order accepted: action=", action,
         " volume=", DoubleToString(volume, 2),
         " deal=", Trade.ResultDeal(),
         " order=", Trade.ResultOrder(),
         " retcode=", Trade.ResultRetcode(),
         " ", Trade.ResultRetcodeDescription()
      );
   }
   return true;
}

void EvaluateAndTrade()
{
   // Hard safety lock: never trade a real/contest account.
   if(!IsDemoAccount())
   {
      Print("DemoAutoTrader BLOCKED: account is not DEMO.");
      return;
   }

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      if(VerboseLogging)
         Print("DemoAutoTrader waiting: Algo Trading / EA trading is not allowed.");
      return;
   }

   datetime current_bar = iTime(_Symbol, PERIOD_M15, 0);
   if(current_bar <= 0 || current_bar == LastExecutedM15Bar)
      return;

   string action;
   string symbol;
   string news_risk;
   int confidence = 0;
   if(!FetchHint(action, confidence, symbol, news_risk))
      return;

   long spread_points = CurrentSpreadPoints();
   if(VerboseLogging)
   {
      Print(
         "DemoAutoTrader signal: action=", action,
         " confidence=", confidence,
         " min=", MinConfidence,
         " news=", news_risk,
         " spread=", spread_points,
         " max_spread=", MaxSpreadPoints,
         " open=", ManagedOpenPositions(),
         "/", MaxOpenTrades
      );
   }

   if(symbol != _Symbol)
   {
      Print("DemoAutoTrader skipped: API symbol ", symbol, " != chart symbol ", _Symbol);
      return;
   }

   // Confidence alone never creates a direction: the API must explicitly return BUY/SELL.
   if(action != "BUY" && action != "SELL")
   {
      if(VerboseLogging)
         Print("DemoAutoTrader skipped: API action is ", action, ".");
      return;
   }

   // Exact threshold is allowed: confidence == MinConfidence passes.
   if(confidence < MinConfidence)
   {
      Print("DemoAutoTrader skipped: confidence ", confidence, " < ", MinConfidence);
      return;
   }

   if(news_risk == "HIGH")
   {
      Print("DemoAutoTrader skipped: HIGH news risk.");
      return;
   }

   if(!SpreadIsAcceptable())
   {
      Print(
         "DemoAutoTrader skipped: spread ", spread_points,
         " > MaxSpreadPoints ", MaxSpreadPoints, "."
      );
      return;
   }

   int already_open = ManagedOpenPositions();
   int capacity = MaxOpenTrades - already_open;
   if(capacity <= 0)
   {
      if(VerboseLogging)
         Print("DemoAutoTrader skipped: MaxOpenTrades reached.");
      return;
   }

   int requested = MathMax(1, TradesPerSignal);
   int to_open = MathMin(requested, capacity);
   int opened = 0;

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(_Symbol);

   for(int i = 0; i < to_open; i++)
   {
      if(OpenManagedTrade(action))
      {
         opened++;
         Print("DemoAutoTrader opened ", action, " #", opened, " confidence=", confidence);
      }
      else
      {
         Print(
            "DemoAutoTrader order failed. retcode=",
            Trade.ResultRetcode(),
            " ",
            Trade.ResultRetcodeDescription()
         );
         break;
      }
   }

   // Mark the bar only after the broker reports an accepted trade result.
   if(opened > 0)
      LastExecutedM15Bar = current_bar;
}

int OnInit()
{
   if(RefreshSeconds < 5 || RequestTimeoutMs < 1000)
      return INIT_PARAMETERS_INCORRECT;
   if(TradesPerSignal < 1 || MaxOpenTrades < 1 || LotSize <= 0.0)
      return INIT_PARAMETERS_INCORRECT;
   if(MinConfidence < 0 || MinConfidence > 100)
      return INIT_PARAMETERS_INCORRECT;

   if(!IsDemoAccount())
   {
      Alert("DemoAutoTrader is DEMO-ONLY and is blocked on this account.");
      return INIT_FAILED;
   }

   Trade.SetAsyncMode(false);
   Trade.SetExpertMagicNumber(MagicNumber);
   Trade.SetDeviationInPoints(SlippagePoints);
   Trade.SetTypeFillingBySymbol(_Symbol);
   EventSetTimer(RefreshSeconds);
   Print(
      "DemoAutoTrader ready on DEMO account. Symbol=", _Symbol,
      " M15-first. MinConfidence=", MinConfidence,
      ". Exact threshold is eligible when API action is BUY/SELL."
   );
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   EvaluateAndTrade();
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}
