#property strict
#property description "ONE EA: MT5 bridge + panel + DEMO auto trader + demo journal"
#include <Trade/Trade.mqh>

input string SymbolName="XAUUSD_o";
input string ApiUrl="http://127.0.0.1:8000/hint";
input int SnapshotBars=100;
input int ContextBars=100;
input int BridgeSeconds=5;
input int SignalSeconds=15;
input int RequestTimeoutMs=45000;
input bool EnableAutoTrading=true;
input int MinConfidence=75;
input double RiskPercent=0.5;
input double RewardRiskRatio=2.0;
input int MaxSpreadPoints=50;
input int MaxOpenTrades=1;
input double AtrMultiplier=1.5;
input int AtrPeriod=14;
input int MinStopPoints=150;
input int MaxStopPoints=1200;
input int SwingLookbackBars=30;
input int SwingLeftBars=2;
input int SwingRightBars=2;
input int StructureBufferPoints=50;
input bool UseAntiChase=true;
input double MaxExtensionAtr=1.5;
input double PullbackZoneAtr=0.35;
input int PullbackMaxBars=4;
input ulong MagicNumber=26090315;
input int SlippagePoints=20;
input bool ExportJournal=true;
input int JournalHistoryDays=180;
input int JournalSeconds=30;
input string SnapshotFile="mt5_snapshot.json";
input string ContextFile="mt5_context.json";
input string JournalFile="demo_trade_journal.csv";
input bool Verbose=true;

const double HARD_MAX_RISK=0.5;
CTrade Trade;
int AtrHandle=INVALID_HANDLE, Ema9Handle=INVALID_HANDLE, Ema21Handle=INVALID_HANDLE;
ulong LastBridgeMs=0, LastSignalMs=0, LastJournalMs=0;
datetime LastExecutedBar=0, PendingBar=0;
string PendingAction="";
bool TradingArmed=false;

struct PosAgg{ulong id;datetime opened;datetime closed;string symbol;string side;double inVol;double outVol;double inValue;double outValue;double sl;double tp;double pnl;};

bool Demo(){return (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;}
string Esc(string s){StringReplace(s,"\\","\\\\");StringReplace(s,"\"","\\\"");return s;}
string Utc(){MqlDateTime x;TimeToStruct(TimeGMT(),x);return StringFormat("%04d-%02d-%02dT%02d:%02d:%02dZ",x.year,x.mon,x.day,x.hour,x.min,x.sec);}
string BrokerTime(datetime t){if(t<=0)return "";MqlDateTime x;TimeToStruct(t,x);return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d",x.year,x.mon,x.day,x.hour,x.min,x.sec);}

string JVal(const string json,const string key){string n="\""+key+"\"";int p=StringFind(json,n);if(p<0)return "";p=StringFind(json,":",p+StringLen(n));if(p<0)return "";p++;int L=StringLen(json);while(p<L){ushort c=StringGetCharacter(json,p);if(c!=' '&&c!='\t')break;p++;}if(p<L&&StringGetCharacter(json,p)=='\"'){int e=StringFind(json,"\"",p+1);if(e<0)return "";return StringSubstr(json,p+1,e-p-1);}int c=StringFind(json,",",p),b=StringFind(json,"}",p),e=c;if(e<0||(b>=0&&b<e))e=b;if(e<0)e=L;return StringSubstr(json,p,e-p);}

bool WriteText(string file,string text){int h=FileOpen(file,FILE_WRITE|FILE_TXT|FILE_ANSI);if(h==INVALID_HANDLE)return false;FileWriteString(h,text);FileClose(h);return true;}
bool CopyDone(ENUM_TIMEFRAMES tf,int bars,MqlRates &r[],int &n,int minbars){ArraySetAsSeries(r,true);n=CopyRates(SymbolName,tf,1,bars,r);return n>=minbars;}
string RF(MqlRates &r[],int n,string f,int d){string s="\""+f+"\":[";for(int i=n-1;i>=0;i--){double v=r[i].close;if(f=="opens")v=r[i].open;else if(f=="highs")v=r[i].high;else if(f=="lows")v=r[i].low;s+=DoubleToString(v,d);if(i>0)s+=",";}return s+"]";}
string TFJ(string name,ENUM_TIMEFRAMES tf,MqlRates &r[],int n,int d){string s="\""+name+"\":{";s+="\"timeframe\":\""+EnumToString(tf)+"\",";s+=RF(r,n,"opens",d)+","+RF(r,n,"highs",d)+","+RF(r,n,"lows",d)+","+RF(r,n,"closes",d);return s+"}";}

datetime DayStart(){MqlDateTime x;TimeToStruct(TimeCurrent(),x);x.hour=0;x.min=0;x.sec=0;return StructToTime(x);}
bool DayMetrics(double &realized,double &startBal){realized=0;startBal=0;if(!HistorySelect(DayStart(),TimeCurrent()))return false;for(int i=0;i<HistoryDealsTotal();i++){ulong t=HistoryDealGetTicket(i);if(!t)continue;ENUM_DEAL_TYPE ty=(ENUM_DEAL_TYPE)HistoryDealGetInteger(t,DEAL_TYPE);if(ty!=DEAL_TYPE_BUY&&ty!=DEAL_TYPE_SELL)continue;realized+=HistoryDealGetDouble(t,DEAL_PROFIT)+HistoryDealGetDouble(t,DEAL_COMMISSION)+HistoryDealGetDouble(t,DEAL_SWAP)+HistoryDealGetDouble(t,DEAL_FEE);}startBal=AccountInfoDouble(ACCOUNT_BALANCE)-realized;return startBal>0;}

bool RefreshBridge(){MqlTick tick;if(!SymbolInfoTick(SymbolName,tick))return false;int d=(int)SymbolInfoInteger(SymbolName,SYMBOL_DIGITS);MqlRates m[];int n=0;if(!CopyDone(PERIOD_M15,SnapshotBars,m,n,21))return false;double rp=0,sb=0;bool dm=DayMetrics(rp,sb);string j="{";j+="\"symbol\":\""+Esc(SymbolName)+"\",\"timeframe\":\"PERIOD_M15\",\"generated_at\":\""+Utc()+"\",";j+="\"bid\":"+DoubleToString(tick.bid,d)+",\"ask\":"+DoubleToString(tick.ask,d)+",";j+="\"balance\":"+DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2)+",\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+",\"positions_total\":"+IntegerToString(PositionsTotal())+",";if(dm)j+="\"day_start_balance\":"+DoubleToString(sb,2)+",\"day_realized_pnl\":"+DoubleToString(rp,2)+",";j+=RF(m,n,"opens",d)+","+RF(m,n,"highs",d)+","+RF(m,n,"lows",d)+","+RF(m,n,"closes",d)+"}";bool ok=WriteText(SnapshotFile,j);MqlRates h1[],h4[];int n1=0,n4=0;if(!CopyDone(PERIOD_H1,ContextBars,h1,n1,65)||!CopyDone(PERIOD_H4,ContextBars,h4,n4,65))return false;string c="{\"symbol\":\""+Esc(SymbolName)+"\",\"generated_at\":\""+Utc()+"\","+TFJ("h1",PERIOD_H1,h1,n1,d)+","+TFJ("h4",PERIOD_H4,h4,n4,d)+"}";return WriteText(ContextFile,c)&&ok;}

void Panel(string status,string json){string a=JVal(json,"action");if(a=="")a="WAIT";string conf=JVal(json,"confidence");if(conf=="")conf="0";string tech=JVal(json,"technical_score");if(tech=="")tech="0";string h1=JVal(json,"h1_trend"),h4=JVal(json,"h4_trend"),mtf=JVal(json,"mtf_status"),news=JVal(json,"news_risk"),cov=JVal(json,"news_coverage"),guard=JVal(json,"risk_guard_status"),dd=JVal(json,"day_drawdown_percent"),sa=JVal(json,"spread_to_atr");if(h1=="")h1="UNAVAILABLE";if(h4=="")h4="UNAVAILABLE";if(mtf=="")mtf="UNAVAILABLE";if(news=="")news="UNKNOWN";if(cov=="")cov="UNAVAILABLE";if(guard=="")guard="UNAVAILABLE";if(dd==""||dd=="null")dd="-";if(sa=="")sa="-";Comment("META TRADER AI | ONE EA\n","Status: ",status," | Auto: ",(TradingArmed?"ARMED DEMO":"OFF"),"\n","Symbol: ",SymbolName," M15\n","Decision: ",a," | Confidence: ",conf,"/100 | Min: ",MinConfidence,"\n","Technical: ",tech," | H1: ",h1," | H4: ",h4," | MTF: ",mtf,"\n","News: ",news," | Coverage: ",cov,"\n","Risk guard: ",guard," | Daily DD: ",dd,"% | Spread/ATR: ",sa,"\n","UTC: ",JVal(json,"generated_at"));}

int VD(double step){if(step>=1)return 0;if(step>=0.1)return 1;if(step>=0.01)return 2;if(step>=0.001)return 3;return 4;}
double VolDown(double req){double mn=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),mx=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),st=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);if(req+1e-12<mn)return 0;double v=MathMin(mx,req);if(st>0)v=mn+MathFloor((v-mn+1e-12)/st)*st;if(v+1e-12<mn)return 0;return NormalizeDouble(v,VD(st));}
int OpenCount(){int c=0;for(int i=PositionsTotal()-1;i>=0;i--){ulong t=PositionGetTicket(i);if(!t||!PositionSelectByTicket(t))continue;if(PositionGetString(POSITION_SYMBOL)==_Symbol&&(ulong)PositionGetInteger(POSITION_MAGIC)==MagicNumber)c++;}return c;}
bool Ind(int h,double &v){v=0;if(h==INVALID_HANDLE)return false;double x[];ArraySetAsSeries(x,true);if(CopyBuffer(h,0,1,1,x)!=1)return false;v=x[0];return v>0;}

bool Swing(string action,ENUM_TIMEFRAMES tf,int look,int l,int r,double &p){p=0;for(int s=r+1;s<=look;s++){double q=action=="BUY"?iLow(_Symbol,tf,s):iHigh(_Symbol,tf,s);if(q<=0)continue;bool ok=true;for(int o=1;o<=l&&ok;o++){double z=action=="BUY"?iLow(_Symbol,tf,s+o):iHigh(_Symbol,tf,s+o);if(z<=0||(action=="BUY"&&q>=z)||(action=="SELL"&&q<=z))ok=false;}for(int o=1;o<=r&&ok;o++){double z=action=="BUY"?iLow(_Symbol,tf,s-o):iHigh(_Symbol,tf,s-o);if(z<=0||(action=="BUY"&&q>z)||(action=="SELL"&&q<z))ok=false;}if(ok){p=q;return true;}}return false;}

bool EntryOK(string action,datetime bar,bool &pullback){pullback=false;if(!UseAntiChase)return true;MqlTick t;if(!SymbolInfoTick(_Symbol,t))return false;double atr,e9,e21;if(!Ind(AtrHandle,atr)||!Ind(Ema9Handle,e9)||!Ind(Ema21Handle,e21))return false;double e=action=="BUY"?t.ask:t.bid;double ext=action=="BUY"?(e-e21)/atr:(e21-e)/atr;if(PendingAction!=""&&PendingAction!=action){PendingAction="";PendingBar=0;}if(PendingAction==""){if(ext>MaxExtensionAtr){PendingAction=action;PendingBar=bar;return false;}return true;}int sh=iBarShift(_Symbol,PERIOD_M15,PendingBar,false);if(sh<0||sh>PullbackMaxBars){PendingAction="";PendingBar=0;return false;}if(ext>MaxExtensionAtr)return false;bool trend=action=="BUY"?e9>e21:e9<e21;double dist=action=="BUY"?(e-e9)/atr:(e9-e)/atr;bool reclaim=action=="BUY"?(e>=e9&&e>=e21):(e<=e9&&e<=e21);if(trend&&reclaim&&dist>=0&&dist<=PullbackZoneAtr){pullback=true;PendingAction="";PendingBar=0;return true;}return false;}

bool Plan(string action,MqlTick &t,double &entry,double &sl,double &tp,double &riskMoney,double &vol){double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);int d=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);double atr;if(point<=0||!Ind(AtrHandle,atr))return false;entry=action=="BUY"?t.ask:t.bid;double pts=MathMax((double)MinStopPoints,(atr*AtrMultiplier)/point);double sw=0;if(Swing(action,PERIOD_M15,SwingLookbackBars,SwingLeftBars,SwingRightBars,sw)){double b=action=="BUY"?sw-StructureBufferPoints*point:sw+StructureBufferPoints*point;double sp=action=="BUY"?(entry-b)/point:(b-entry)/point;if(sp>pts&&(MaxStopPoints<=0||sp<=MaxStopPoints))pts=sp;}long broker=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);pts=MathMax(pts,(double)broker+5);if(MaxStopPoints>0&&pts>MaxStopPoints)return false;sl=NormalizeDouble(action=="BUY"?entry-pts*point:entry+pts*point,d);tp=NormalizeDouble(action=="BUY"?entry+pts*RewardRiskRatio*point:entry-pts*RewardRiskRatio*point,d);double one=0;ENUM_ORDER_TYPE ot=action=="BUY"?ORDER_TYPE_BUY:ORDER_TYPE_SELL;if(!OrderCalcProfit(ot,_Symbol,1.0,entry,sl,one)||MathAbs(one)<=0)return false;double pct=MathMin(RiskPercent,HARD_MAX_RISK);riskMoney=AccountInfoDouble(ACCOUNT_EQUITY)*pct/100.0;vol=VolDown(riskMoney/MathAbs(one));return vol>0;}

void MaybeTrade(string json){if(!TradingArmed||!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)||!MQLInfoInteger(MQL_TRADE_ALLOWED))return;datetime bar=iTime(_Symbol,PERIOD_M15,0);if(bar<=0||bar==LastExecutedBar)return;string a=JVal(json,"action"),sym=JVal(json,"symbol"),news=JVal(json,"news_risk");int conf=(int)StringToInteger(JVal(json,"confidence"));if(sym!=_Symbol||(a!="BUY"&&a!="SELL")||conf<MinConfidence||news=="HIGH")return;if(MaxSpreadPoints>0&&SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)>MaxSpreadPoints)return;if(OpenCount()>=MaxOpenTrades)return;bool pb=false;if(!EntryOK(a,bar,pb))return;MqlTick t;if(!SymbolInfoTick(_Symbol,t))return;double en,sl,tp,rm,vol;if(!Plan(a,t,en,sl,tp,rm,vol))return;Trade.SetAsyncMode(false);Trade.SetExpertMagicNumber(MagicNumber);Trade.SetDeviationInPoints(SlippagePoints);Trade.SetTypeFillingBySymbol(_Symbol);bool ok=a=="BUY"?Trade.Buy(vol,_Symbol,0,sl,tp,"MetaTraderAI"):Trade.Sell(vol,_Symbol,0,sl,tp,"MetaTraderAI");if(ok&&(Trade.ResultRetcode()==TRADE_RETCODE_DONE||Trade.ResultRetcode()==TRADE_RETCODE_DONE_PARTIAL||Trade.ResultRetcode()==TRADE_RETCODE_PLACED)){LastExecutedBar=bar;if(Verbose)Print("MetaTraderAI opened ",a," vol=",DoubleToString(vol,3)," risk=$",DoubleToString(rm,2)," SL=",DoubleToString(sl,_Digits)," TP=",DoubleToString(tp,_Digits));}}

bool Fetch(string &resp,int &code){char req[],out[];string hdr;ArrayResize(req,0);ResetLastError();code=WebRequest("GET",ApiUrl,"",RequestTimeoutMs,req,out,hdr);if(code==-1){resp="";return false;}resp=CharArrayToString(out,0,-1,CP_UTF8);return code==200;}
void RefreshSignal(){RefreshBridge();LastBridgeMs=GetTickCount64();string r;int c;if(!Fetch(r,c)){Panel(c==-1?"API ERROR":"HTTP "+IntegerToString(c),"{}");return;}Panel("CONNECTED",r);MaybeTrade(r);}

int FindPos(PosAgg &a[],ulong id){for(int i=0;i<ArraySize(a);i++)if(a[i].id==id)return i;return -1;}
int AddPos(PosAgg &a[],ulong id){int x=FindPos(a,id);if(x>=0)return x;int i=ArraySize(a);ArrayResize(a,i+1);a[i].id=id;a[i].opened=0;a[i].closed=0;a[i].symbol="";a[i].side="";a[i].inVol=a[i].outVol=a[i].inValue=a[i].outValue=a[i].sl=a[i].tp=a[i].pnl=0;return i;}

void Journal(){if(!ExportJournal||!Demo())return;datetime now=TimeCurrent(),from=now-(datetime)(MathMax(1,JournalHistoryDays)*86400);if(!HistorySelect(from,now))return;PosAgg a[];for(int i=0;i<HistoryDealsTotal();i++){ulong d=HistoryDealGetTicket(i);if(!d||(ulong)HistoryDealGetInteger(d,DEAL_MAGIC)!=MagicNumber||HistoryDealGetString(d,DEAL_SYMBOL)!=SymbolName)continue;ENUM_DEAL_TYPE ty=(ENUM_DEAL_TYPE)HistoryDealGetInteger(d,DEAL_TYPE);if(ty!=DEAL_TYPE_BUY&&ty!=DEAL_TYPE_SELL)continue;ulong id=(ulong)HistoryDealGetInteger(d,DEAL_POSITION_ID);if(!id)continue;int x=AddPos(a,id);ENUM_DEAL_ENTRY ek=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(d,DEAL_ENTRY);double v=HistoryDealGetDouble(d,DEAL_VOLUME),p=HistoryDealGetDouble(d,DEAL_PRICE);datetime tm=(datetime)HistoryDealGetInteger(d,DEAL_TIME);a[x].symbol=SymbolName;a[x].pnl+=HistoryDealGetDouble(d,DEAL_PROFIT)+HistoryDealGetDouble(d,DEAL_COMMISSION)+HistoryDealGetDouble(d,DEAL_SWAP)+HistoryDealGetDouble(d,DEAL_FEE);if(ek==DEAL_ENTRY_IN){a[x].inVol+=v;a[x].inValue+=p*v;if(a[x].opened==0||tm<a[x].opened)a[x].opened=tm;if(a[x].side=="")a[x].side=ty==DEAL_TYPE_BUY?"BUY":"SELL";ulong o=(ulong)HistoryDealGetInteger(d,DEAL_ORDER);if(o>0&&a[x].sl<=0){a[x].sl=HistoryOrderGetDouble(o,ORDER_SL);a[x].tp=HistoryOrderGetDouble(o,ORDER_TP);}}else if(ek==DEAL_ENTRY_OUT||ek==DEAL_ENTRY_OUT_BY){a[x].outVol+=v;a[x].outValue+=p*v;if(tm>a[x].closed)a[x].closed=tm;}}
int h=FileOpen(JournalFile,FILE_WRITE|FILE_CSV|FILE_ANSI,',');if(h==INVALID_HANDLE)return;FileWrite(h,"position_id","opened_at_broker","closed_at_broker","symbol","side","volume","entry_price","exit_price","initial_sl","initial_tp","net_pnl","planned_risk_money","pnl_r","outcome","magic");for(int i=0;i<ArraySize(a);i++){if(a[i].inVol<=0||a[i].closed<=0||a[i].outVol+1e-8<a[i].inVol)continue;double en=a[i].inValue/a[i].inVol,ex=a[i].outValue/a[i].outVol,risk=0;ENUM_ORDER_TYPE ot=a[i].side=="BUY"?ORDER_TYPE_BUY:ORDER_TYPE_SELL;double x=0;bool hr=a[i].sl>0&&OrderCalcProfit(ot,a[i].symbol,a[i].inVol,en,a[i].sl,x)&&MathAbs(x)>0;if(hr)risk=MathAbs(x);int dg=(int)SymbolInfoInteger(a[i].symbol,SYMBOL_DIGITS);FileWrite(h,StringFormat("%I64u",a[i].id),BrokerTime(a[i].opened),BrokerTime(a[i].closed),a[i].symbol,a[i].side,DoubleToString(a[i].inVol,4),DoubleToString(en,dg),DoubleToString(ex,dg),a[i].sl>0?DoubleToString(a[i].sl,dg):"",a[i].tp>0?DoubleToString(a[i].tp,dg):"",DoubleToString(a[i].pnl,2),hr?DoubleToString(risk,2):"",hr?DoubleToString(a[i].pnl/risk,6):"",a[i].pnl>1e-8?"WIN":a[i].pnl<-1e-8?"LOSS":"FLAT",StringFormat("%I64u",MagicNumber));}FileClose(h);}

int OnInit(){if(_Symbol!=SymbolName||_Period!=PERIOD_M15){Alert("Attach MetaTraderAI only to ",SymbolName," M15 chart.");return INIT_FAILED;}if(SnapshotBars<21||ContextBars<65||RiskPercent<=0||RiskPercent>HARD_MAX_RISK||RewardRiskRatio<=0)return INIT_PARAMETERS_INCORRECT;if(!SymbolSelect(SymbolName,true))return INIT_FAILED;TradingArmed=EnableAutoTrading&&Demo();if(EnableAutoTrading&&!Demo())Alert("Auto trading is DEMO-only; bridge/panel stay active.");AtrHandle=iATR(_Symbol,PERIOD_M15,AtrPeriod);Ema9Handle=iMA(_Symbol,PERIOD_M15,9,0,MODE_EMA,PRICE_CLOSE);Ema21Handle=iMA(_Symbol,PERIOD_M15,21,0,MODE_EMA,PRICE_CLOSE);if(AtrHandle==INVALID_HANDLE||Ema9Handle==INVALID_HANDLE||Ema21Handle==INVALID_HANDLE)return INIT_FAILED;Trade.SetExpertMagicNumber(MagicNumber);EventSetMillisecondTimer(500);RefreshBridge();Journal();RefreshSignal();ulong n=GetTickCount64();LastBridgeMs=LastSignalMs=LastJournalMs=n;Print("MetaTraderAI ready: ONE EA on ONE chart. Auto=",TradingArmed?"ARMED":"OFF");return INIT_SUCCEEDED;}
void OnTimer(){ulong n=GetTickCount64();if(n-LastBridgeMs>=(ulong)BridgeSeconds*1000){RefreshBridge();LastBridgeMs=n;}if(n-LastSignalMs>=(ulong)SignalSeconds*1000){RefreshSignal();LastSignalMs=GetTickCount64();}if(ExportJournal&&GetTickCount64()-LastJournalMs>=(ulong)JournalSeconds*1000){Journal();LastJournalMs=GetTickCount64();}}
void OnDeinit(const int reason){EventKillTimer();Comment("");if(AtrHandle!=INVALID_HANDLE)IndicatorRelease(AtrHandle);if(Ema9Handle!=INVALID_HANDLE)IndicatorRelease(Ema9Handle);if(Ema21Handle!=INVALID_HANDLE)IndicatorRelease(Ema21Handle);}
