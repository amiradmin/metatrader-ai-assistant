#property strict
#property description "Read-only API signal panel. Contains no order functions."

input string ApiUrl = "http://127.0.0.1:8000/hint";
input int RefreshSeconds = 60;
input int RequestTimeoutMs = 45000;

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

void ShowPanel(
   const string status,
   const string action,
   const string symbol,
   const string confidence,
   const string technical_score,
   const string news_risk,
   const string generated_at
)
{
   string guidance = "NO NEW TRADE";
   if(action == "BUY")
      guidance = "BUY BIAS ONLY - MANUAL CONFIRMATION REQUIRED";
   else if(action == "SELL")
      guidance = "SELL BIAS ONLY - MANUAL CONFIRMATION REQUIRED";

   Comment(
      "AI TRADING ASSISTANT - READ ONLY\n",
      "Status: ", status, "\n",
      "Symbol: ", symbol, "\n",
      "Decision: ", action, "\n",
      "Confidence: ", confidence, " / 100\n",
      "Technical score: ", technical_score, "\n",
      "News risk: ", news_risk, "\n",
      "Guidance: ", guidance, "\n",
      "Generated UTC: ", generated_at, "\n\n",
      "This panel never places or modifies orders."
   );
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
         "-"
      );
      Print(
         "ReadOnlySignalPanel WebRequest failed: ",
         error_code,
         ". Add ",
         ApiUrl,
         " under Tools > Options > Expert Advisors > Allow WebRequest."
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
   string generated_at = JsonValue(response, "generated_at");

   if(action == "" || symbol == "")
   {
      ShowPanel("INVALID RESPONSE", "WAIT", _Symbol, "0", "0", "UNKNOWN", "-");
      return false;
   }

   ShowPanel(
      "CONNECTED",
      action,
      symbol,
      confidence,
      technical_score,
      news_risk,
      generated_at
   );
   return true;
}

int OnInit()
{
   if(RefreshSeconds < 5 || RequestTimeoutMs < 1000)
      return INIT_PARAMETERS_INCORRECT;

   EventSetTimer(RefreshSeconds);
   ShowPanel("CONNECTING", "WAIT", _Symbol, "0", "0", "UNKNOWN", "-");
   RefreshHint();
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   RefreshHint();
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}
