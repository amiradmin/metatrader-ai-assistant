#property strict
#property description "Readable read-only API signal panel. Contains no order functions."

#include <Canvas\Canvas.mqh>

input string ApiUrl = "http://127.0.0.1:8000/hint";
input int RefreshSeconds = 60;
input int RequestTimeoutMs = 45000;
input int PanelWidth = 460;
input int PanelHeight = 380;
input int PanelFontSize = 14;
input int PanelLeft = 20;
input int PanelTop = 170;
input int PanelOpacityPercent = 75;

string Prefix = "ReadOnlySignalPanel_";
color CurrentActionColor = clrGold;
bool BlinkVisible = true;
ulong LastApiRefreshMs = 0;
CCanvas PanelCanvas;
bool PanelCanvasReady = false;

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

void CreateBackground()
{
   string name = Prefix + "Background";
   if(!PanelCanvasReady)
   {
      if(ObjectFind(0, name) >= 0)
         ObjectDelete(0, name);

      PanelCanvasReady = PanelCanvas.CreateBitmapLabel(
         0,
         0,
         name,
         PanelLeft,
         PanelTop,
         PanelWidth,
         PanelHeight,
         COLOR_FORMAT_ARGB_NORMALIZE
      );
      if(!PanelCanvasReady)
      {
         Print("ReadOnlySignalPanel could not create the alpha background: ", GetLastError());
         return;
      }

      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_ZORDER, 0);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }

   int opacity = MathMax(0, MathMin(100, PanelOpacityPercent));
   uchar alpha = (uchar)MathRound(255.0 * opacity / 100.0);
   PanelCanvas.Erase(ColorToARGB(C'20,24,31', alpha));
   PanelCanvas.Rectangle(
      0,
      0,
      PanelWidth - 1,
      PanelHeight - 1,
      ColorToARGB(C'80,90,105', alpha)
   );
   PanelCanvas.Update();
}

void SetLine(
   const string id,
   const string text,
   const int y,
   const int font_size,
   const color text_color
)
{
   string name = Prefix + id;
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);

   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, PanelLeft + 22);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, PanelTop + y - 40);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetInteger(0, name, OBJPROP_COLOR, text_color);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_ZORDER, 10);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   ObjectSetString(0, name, OBJPROP_FONT, "DejaVu Sans");
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void ShowPanel(
   const string status,
   const string action,
   const string symbol,
   const string confidence,
   const string technical_score,
   const string news_risk,
   const string tipranks_status,
   const string tipranks_adjustment,
   const string generated_at
)
{
   CreateBackground();

   color action_color = clrGold;
   string guidance = "NO NEW TRADE";
   if(action == "BUY")
   {
      action_color = clrLime;
      guidance = "BUY BIAS - MANUAL CONFIRMATION REQUIRED";
   }
   else if(action == "SELL")
   {
      action_color = clrTomato;
      guidance = "SELL BIAS - MANUAL CONFIRMATION REQUIRED";
   }

   color tipranks_color = clrSilver;
   if(tipranks_status == "CONFIRM")
      tipranks_color = clrLime;
   else if(tipranks_status == "OPPOSE")
      tipranks_color = clrTomato;
   else if(tipranks_status == "NEUTRAL")
      tipranks_color = clrGold;

   CurrentActionColor = action_color;
   BlinkVisible = true;

   SetLine("Hello", "Hello Amir", 58, PanelFontSize + 5, clrWhite);
   SetLine("Title", "AI TRADING ASSISTANT  |  READ ONLY", 92, PanelFontSize, clrDeepSkyBlue);
   SetLine("Status", "Status: " + status, 126, PanelFontSize, clrWhite);
   SetLine("Symbol", "Symbol: " + symbol + "  |  M15", 158, PanelFontSize, clrWhite);
   SetLine("Decision", "Decision: " + action, 190, PanelFontSize + 3, action_color);
   SetLine("Confidence", "Confidence: " + confidence + " / 100", 226, PanelFontSize, clrWhite);
   SetLine("Technical", "Technical score: " + technical_score, 258, PanelFontSize, clrWhite);
   SetLine("News", "News risk: " + news_risk, 290, PanelFontSize, clrWhite);
   SetLine(
      "TipRanks",
      "TipRanks: " + tipranks_status + "  (" + tipranks_adjustment + ")",
      322,
      PanelFontSize,
      tipranks_color
   );
   SetLine("Guidance", guidance, 354, PanelFontSize - 1, action_color);
   SetLine("Time", "UTC: " + generated_at, 382, PanelFontSize - 2, clrWhite);
   ChartRedraw();
}

void DeletePanel()
{
   if(PanelCanvasReady)
   {
      PanelCanvas.Destroy();
      PanelCanvasReady = false;
   }
   else
      ObjectDelete(0, Prefix + "Background");
   ObjectDelete(0, Prefix + "Hello");
   ObjectDelete(0, Prefix + "Title");
   ObjectDelete(0, Prefix + "Status");
   ObjectDelete(0, Prefix + "Symbol");
   ObjectDelete(0, Prefix + "Decision");
   ObjectDelete(0, Prefix + "Confidence");
   ObjectDelete(0, Prefix + "Technical");
   ObjectDelete(0, Prefix + "News");
   ObjectDelete(0, Prefix + "TipRanks");
   ObjectDelete(0, Prefix + "Guidance");
   ObjectDelete(0, Prefix + "Time");
}

void BlinkDecision()
{
   BlinkVisible = !BlinkVisible;
   color visible_color = BlinkVisible ? CurrentActionColor : C'20,24,31';
   ObjectSetInteger(0, Prefix + "Decision", OBJPROP_COLOR, visible_color);
   ChartRedraw();
}

bool RefreshHint()
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
      int error_code = GetLastError();
      ShowPanel(
         "API ERROR " + IntegerToString(error_code),
         "WAIT",
         _Symbol,
         "0",
         "0",
         "UNKNOWN",
         "UNAVAILABLE",
         "0",
         "-"
      );
      Print(
         "ReadOnlySignalPanel WebRequest failed: ",
         error_code,
         ". Add http://127.0.0.1:8000 under Tools > Options > Expert Advisors."
      );
      return false;
   }

   string response = CharArrayToString(response_data, 0, -1, CP_UTF8);
   if(status_code != 200)
   {
      ShowPanel(
         "HTTP " + IntegerToString(status_code),
         "WAIT",
         _Symbol,
         "0",
         "0",
         "UNKNOWN",
         "UNAVAILABLE",
         "0",
         "-"
      );
      Print("ReadOnlySignalPanel API response: ", response);
      return false;
   }

   string action = JsonValue(response, "action");
   string symbol = JsonValue(response, "symbol");
   string confidence = JsonValue(response, "confidence");
   string technical_score = JsonValue(response, "technical_score");
   string news_risk = JsonValue(response, "news_risk");
   string tipranks_status = JsonValue(response, "tipranks_status");
   string tipranks_adjustment = JsonValue(response, "tipranks_adjustment");
   string generated_at = JsonValue(response, "generated_at");

   if(action == "" || symbol == "")
   {
      ShowPanel(
         "INVALID RESPONSE",
         "WAIT",
         _Symbol,
         "0",
         "0",
         "UNKNOWN",
         "UNAVAILABLE",
         "0",
         "-"
      );
      return false;
   }

   if(tipranks_status == "")
      tipranks_status = "UNAVAILABLE";
   if(tipranks_adjustment == "")
      tipranks_adjustment = "0";

   ShowPanel(
      "CONNECTED",
      action,
      symbol,
      confidence,
      technical_score,
      news_risk,
      tipranks_status,
      tipranks_adjustment,
      generated_at
   );
   return true;
}

int OnInit()
{
   if(RefreshSeconds < 5 || RequestTimeoutMs < 1000 || PanelOpacityPercent < 0 || PanelOpacityPercent > 100)
      return INIT_PARAMETERS_INCORRECT;

   EventSetMillisecondTimer(500);
   ShowPanel(
      "CONNECTING",
      "WAIT",
      _Symbol,
      "0",
      "0",
      "UNKNOWN",
      "UNAVAILABLE",
      "0",
      "-"
   );
   RefreshHint();
   LastApiRefreshMs = GetTickCount64();
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   BlinkDecision();

   ulong now_ms = GetTickCount64();
   if(now_ms - LastApiRefreshMs >= (ulong)RefreshSeconds * 1000)
   {
      RefreshHint();
      LastApiRefreshMs = now_ms;
   }
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DeletePanel();
   ChartRedraw();
}
