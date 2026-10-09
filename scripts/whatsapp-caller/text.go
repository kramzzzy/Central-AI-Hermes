package main

import (
 "context"
 "crypto/hmac"
 "crypto/rand"
 "crypto/sha256"
 "database/sql"
 "encoding/base64"
 "encoding/hex"
 "encoding/json"
 "errors"
 "fmt"
 "log"
 "mime"
 "net/http"
 "os"
 "path/filepath"
 "regexp"
 "strings"
 "sync"
 "time"

 wa "github.com/polymorfa/hypermeow"
 "github.com/polymorfa/hypermeow/proto/waCommon"
 "github.com/polymorfa/hypermeow/proto/waE2E"
 "github.com/polymorfa/hypermeow/types"
 "github.com/polymorfa/hypermeow/types/events"
 "google.golang.org/protobuf/proto"
)

type textPacket struct {
 ChatID string `json:"chatId"`
 WireChat string `json:"wireChat,omitempty"`
 SenderID string `json:"senderId"`
 SenderName string `json:"senderName"`
 MessageID string `json:"messageId"`
 Body string `json:"body"`
 IsGroup bool `json:"isGroup"`
 BotIDs []string `json:"botIds"`
 MentionedIDs []string `json:"mentionedIds,omitempty"`
 HasQuotedMessage bool `json:"hasQuotedMessage"`
 QuotedMessageID string `json:"quotedMessageId,omitempty"`
 QuotedParticipant string `json:"quotedParticipant,omitempty"`
 QuotedText string `json:"quotedText,omitempty"`
 HasMedia bool `json:"hasMedia"`
 MediaType string `json:"mediaType,omitempty"`
 MediaURLs []string `json:"mediaUrls,omitempty"`
 MIME string `json:"mimetype,omitempty"`
 Lease string `json:"bridgeLease,omitempty"`
}

type textBridge struct {
 db *sql.DB
 key string
 group string
 activated int64
 enabled string
 send func(context.Context,types.JID,*waE2E.Message)(string,error)
 upload func(context.Context,[]byte,wa.MediaType)(wa.UploadResponse,error)
 setPresence func(context.Context,types.JID,types.ChatPresence,types.ChatPresenceMedia)error
 guard sync.Mutex
}

func newTextBridge(path,key,group,enabled string,send func(context.Context,types.JID,*waE2E.Message)(string,error))(*textBridge,error){
 if group!="" && !regexp.MustCompile(`^[0-9]+(-[0-9]+)?@g\.us$`).MatchString(group){return nil,errors.New("invalid selected text group")}
 db,err:=sql.Open("sqlite","file:"+path+"?_pragma=busy_timeout(5000)");if err!=nil{return nil,err}
 db.SetMaxOpenConns(1)
 for _,query:=range []string{
  `CREATE TABLE IF NOT EXISTS text_meta (key TEXT PRIMARY KEY,value INTEGER NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS text_inbox (id TEXT PRIMARY KEY,route TEXT NOT NULL,chat TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',lease TEXT UNIQUE,created INTEGER NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS text_outbox (key TEXT PRIMARY KEY,lease TEXT NOT NULL,chat TEXT NOT NULL,status TEXT NOT NULL,message_id TEXT,created INTEGER NOT NULL)`,
  `CREATE INDEX IF NOT EXISTS text_inbox_pending ON text_inbox(route,status,created)`,
  `UPDATE text_inbox SET status='interrupted' WHERE status='claimed'`,
  `UPDATE text_outbox SET status='unconfirmed' WHERE status='sending'`,
 }{if _,err=db.Exec(query);err!=nil{db.Close();return nil,err}}
 os.Chmod(path,0600)
 var activated int64
 err=db.QueryRow(`SELECT value FROM text_meta WHERE key='activated'`).Scan(&activated)
 if err!=nil && err!=sql.ErrNoRows{db.Close();return nil,err}
 return &textBridge{db:db,key:key,group:group,activated:activated,enabled:enabled,send:send},nil
}

func(b *textBridge)ready()bool{
 if b.enabled!=""{
  info,err:=os.Stat(b.enabled)
  if err!=nil || !info.Mode().IsRegular(){
   alt:="/data/remote-engine-enabled"
   if b.enabled==alt{alt="/data/text-engine-enabled"}
   info2,err2:=os.Stat(alt)
   if err2!=nil || !info2.Mode().IsRegular(){return false}
  }
 }
 b.guard.Lock();defer b.guard.Unlock()
 if b.activated==0{
  now:=time.Now().Unix()
  if _,err:=b.db.Exec(`INSERT OR IGNORE INTO text_meta(key,value) VALUES('activated',?)`,now);err!=nil{return false}
  if err:=b.db.QueryRow(`SELECT value FROM text_meta WHERE key='activated'`).Scan(&b.activated);err!=nil{return false}
 }
 return true
}

func(b *textBridge)route(packet textPacket)string{
 if packet.IsGroup{
  if cfg, managed := managedPhoneRouting(); managed && cfg.Group != "" {
   if packet.ChatID == cfg.Group { return "team" }
  }
  if b.group!="" && packet.ChatID==b.group{return "team"}
  return ""
 }
 if packet.ChatID!=packet.SenderID+"@s.whatsapp.net"{return ""}
 return configuredTextRoute(packet.SenderID)
}

func(b *textBridge)queue(packet textPacket,at time.Time)bool{
 if !b.ready() || at.Unix()<b.activated || at.After(time.Now().Add(time.Minute)) || packet.MessageID=="" || len(packet.Body)>65536{return false}
 route:=b.route(packet);if route==""{return false}
 if packet.IsGroup{
  addressed:=regexp.MustCompile(`(?i)\bleo\b`).MatchString(packet.Body)
  for _,bot:=range packet.BotIDs{
   if packet.QuotedParticipant==bot{addressed=true}
   for _,mentioned:=range packet.MentionedIDs{if bot==mentioned{addressed=true}}
  }
  if !addressed{return false}
 }
 payload,err:=json.Marshal(packet);if err!=nil{return false}
 result,err:=b.db.Exec(`INSERT OR IGNORE INTO text_inbox(id,route,chat,payload,created) VALUES(?,?,?,?,?)`,packet.ChatID+"/"+packet.MessageID,route,packet.ChatID,string(payload),at.Unix())
 if err!=nil{log.Print("Leo text inbox persistence failed");return false}
 n,_:=result.RowsAffected();return n==1
}

func(b *textBridge)claim(route string)([]textPacket,error){
 b.guard.Lock();defer b.guard.Unlock()
 tx,err:=b.db.Begin();if err!=nil{return nil,err};defer tx.Rollback()
 var rows *sql.Rows
 if route=="owner"{
  rows,err=tx.Query(`SELECT id,payload FROM text_inbox WHERE (route='owner' OR route='mark' OR route LIKE 'contact-%') AND status='queued' ORDER BY created,id LIMIT 8`)
 } else {
  rows,err=tx.Query(`SELECT id,payload FROM text_inbox WHERE route=? AND status='queued' ORDER BY created,id LIMIT 8`,route)
 }
 if err!=nil{return nil,err}
 type entry struct{id,body string};entries:=[]entry{}
 for rows.Next(){var e entry;if err=rows.Scan(&e.id,&e.body);err!=nil{rows.Close();return nil,err};entries=append(entries,e)}
 if err=rows.Err();err!=nil{rows.Close();return nil,err};rows.Close()
 packets:=[]textPacket{}
 for _,e:=range entries{
  var packet textPacket;if err=json.Unmarshal([]byte(e.body),&packet);err!=nil{return nil,err}
  token:=make([]byte,24);if _,err=rand.Read(token);err!=nil{return nil,err};packet.Lease=hex.EncodeToString(token)
  if _,err=tx.Exec(`UPDATE text_inbox SET status='claimed',lease=? WHERE id=? AND status='queued'`,packet.Lease,e.id);err!=nil{return nil,err}
  packets=append(packets,packet)
 }
 if err=tx.Commit();err!=nil{return nil,err};return packets,nil
}

func(b *textBridge)binding(route,lease,chat string)(textPacket,error){
 var body string;var packet textPacket
 err:=b.db.QueryRow(`SELECT payload FROM text_inbox WHERE (route=? OR ?='owner') AND lease=? AND chat=? AND status IN ('claimed','complete')`,route,route,lease,chat).Scan(&body)
 if err!=nil{return packet,errors.New("reply binding unavailable")}
 err=json.Unmarshal([]byte(body),&packet);return packet,err
}

func(b *textBridge)serve(w http.ResponseWriter,r *http.Request){
 w.Header().Set("Content-Type","application/json")
 reply:=func(code int,value any){w.WriteHeader(code);json.NewEncoder(w).Encode(value)}
 if !hmac.Equal([]byte(r.Header.Get("Authorization")),[]byte("Bearer "+b.key)){reply(401,map[string]string{"error":"Unauthorized"});return}
 route:=r.Header.Get("X-Leo-Route");if !configuredRouteAllowed(route){reply(403,map[string]string{"error":"Route rejected"});return}
 if !b.ready(){reply(503,map[string]string{"error":"Text engine is in standby"});return}
 action:=strings.TrimPrefix(r.URL.Path,"/text/")
 if r.Method=="GET" && action=="health"{reply(200,map[string]string{"status":"connected"});return}
 if r.Method=="GET" && action=="messages"{packets,err:=b.claim(route);if err!=nil{reply(503,map[string]string{"error":"Inbox unavailable"});return};reply(200,packets);return}
 if r.Method!="POST"{reply(405,map[string]string{"error":"Method rejected"});return}
 var err error
 var body struct{ChatID string `json:"chatId"`;Message string `json:"message"`;MessageID string `json:"messageId"`;Lease string `json:"bridgeLease"`;SendKey string `json:"sendKey"`;Status string `json:"status"`;Presence string `json:"presence"`;Media string `json:"mediaBase64"`;MediaType string `json:"mediaType"`;MIME string `json:"mimetype"`;FileName string `json:"fileName"`}
 if err:=json.NewDecoder(http.MaxBytesReader(w,r.Body,24*1024*1024)).Decode(&body);err!=nil{reply(400,map[string]string{"error":"Invalid body"});return}
 if action=="complete"{
  if b.setPresence!=nil && body.ChatID!=""{
   if t,e:=types.ParseJID(body.ChatID);e==nil && t.Server!=""{
    _ = b.setPresence(r.Context(),t,types.ChatPresencePaused,types.ChatPresenceMediaText)
   }
  }
  status:="complete";if body.Status!="complete"{status="interrupted"}
  if _,err=b.db.Exec(`UPDATE text_inbox SET status=? WHERE (route=? OR ?='owner') AND lease=?`,status,route,route,body.Lease);err!=nil{reply(503,map[string]string{"error":"Receipt persistence failed"});return}
  reply(200,map[string]bool{"success":true});return
 }
 if action=="typing"{
  targetStr:=body.ChatID
  if body.Lease!=""{
   if packet,err:=b.binding(route,body.Lease,body.ChatID);err==nil && packet.WireChat!=""{
    targetStr=packet.WireChat
   }
  }
  target,err:=types.ParseJID(targetStr)
  if err==nil && target.Server!="" && b.setPresence!=nil{
   state:=types.ChatPresenceComposing
   if strings.EqualFold(body.Presence,"paused"){
    state=types.ChatPresencePaused
   }
   ctx,cancel:=context.WithTimeout(context.Background(),5*time.Second)
   defer cancel()
   _ = b.setPresence(ctx,target,state,types.ChatPresenceMediaText)
  }
  reply(200,map[string]bool{"success":true})
  return
 }
 packet,err:=b.binding(route,body.Lease,body.ChatID);if err!=nil{reply(403,map[string]string{"error":"Reply target rejected"});return}
 if action!="send" && action!="edit" && action!="send-media"{reply(422,map[string]string{"error":"This message format is not available"});return}
 if (action!="send-media" && len(strings.TrimSpace(body.Message))==0) || len(body.Message)>65536 || !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(body.SendKey){reply(400,map[string]string{"error":"Invalid reply"});return}
 var attachment []byte;mediaType:=wa.MediaDocument
 if action=="send-media"{
  attachment,err=base64.StdEncoding.DecodeString(body.Media)
  if err!=nil || len(attachment)==0 || len(attachment)>16*1024*1024 || b.upload==nil{reply(400,map[string]string{"error":"Attachment unavailable or exceeds 16 MB"});return}
  switch body.MediaType{
  case "image":mediaType=wa.MediaImage
  case "audio":mediaType=wa.MediaAudio
  case "document":mediaType=wa.MediaDocument
  default:mediaType=wa.MediaDocument
  }
 }
 key:=route+"/"+body.Lease+"/"+action+"/"+body.SendKey
 b.guard.Lock()
 var status,id string
 err=b.db.QueryRow(`SELECT status,COALESCE(message_id,'') FROM text_outbox WHERE key=?`,key).Scan(&status,&id)
 if err==nil{b.guard.Unlock();if status=="sent"{reply(200,map[string]string{"messageId":id});return};reply(409,map[string]string{"error":"Delivery unconfirmed; do not retry"});return}
 if err!=sql.ErrNoRows{b.guard.Unlock();reply(503,map[string]string{"error":"Outbox unavailable"});return}
 if action=="edit"{
  var owner int
  if b.db.QueryRow(`SELECT COUNT(*) FROM text_outbox WHERE lease=? AND chat=? AND status='sent' AND message_id=?`,body.Lease,body.ChatID,body.MessageID).Scan(&owner)!=nil || owner!=1{b.guard.Unlock();reply(403,map[string]string{"error":"Edit target rejected"});return}
 }
 _,err=b.db.Exec(`INSERT INTO text_outbox(key,lease,chat,status,created) VALUES(?,?,?,'sending',?)`,key,body.Lease,body.ChatID,time.Now().Unix());b.guard.Unlock()
 if err!=nil{reply(503,map[string]string{"error":"Outbox persistence failed"});return}
 target,err:=types.ParseJID(packet.WireChat);if err!=nil || target.Server==""{b.db.Exec(`UPDATE text_outbox SET status='unconfirmed' WHERE key=?`,key);reply(400,map[string]string{"error":"Transport target rejected"});return}
 message:=&waE2E.Message{Conversation:proto.String(body.Message)}
 if action=="edit"{message=&waE2E.Message{ProtocolMessage:&waE2E.ProtocolMessage{Type:waE2E.ProtocolMessage_MESSAGE_EDIT.Enum(),Key:&waCommon.MessageKey{RemoteJID:proto.String(packet.WireChat),FromMe:proto.Bool(true),ID:proto.String(body.MessageID)},EditedMessage:message,TimestampMS:proto.Int64(time.Now().UnixMilli())}}}
 ctx,cancel:=context.WithTimeout(r.Context(),25*time.Second);defer cancel()
 if action=="send-media"{
  uploaded,uploadErr:=b.upload(ctx,attachment,mediaType)
  if uploadErr!=nil{b.db.Exec(`UPDATE text_outbox SET status='unconfirmed' WHERE key=?`,key);reply(409,map[string]string{"error":"Attachment delivery unconfirmed; do not retry"});return}
  switch body.MediaType{
  case "image":message=&waE2E.Message{ImageMessage:&waE2E.ImageMessage{URL:proto.String(uploaded.URL),DirectPath:proto.String(uploaded.DirectPath),MediaKey:uploaded.MediaKey,FileSHA256:uploaded.FileSHA256,FileEncSHA256:uploaded.FileEncSHA256,FileLength:proto.Uint64(uint64(len(attachment))),Mimetype:proto.String(body.MIME),Caption:proto.String(body.Message)}}
  case "audio":message=&waE2E.Message{AudioMessage:&waE2E.AudioMessage{URL:proto.String(uploaded.URL),DirectPath:proto.String(uploaded.DirectPath),MediaKey:uploaded.MediaKey,FileSHA256:uploaded.FileSHA256,FileEncSHA256:uploaded.FileEncSHA256,FileLength:proto.Uint64(uint64(len(attachment))),Mimetype:proto.String(body.MIME)}}
  case "document":message=&waE2E.Message{DocumentMessage:&waE2E.DocumentMessage{URL:proto.String(uploaded.URL),DirectPath:proto.String(uploaded.DirectPath),MediaKey:uploaded.MediaKey,FileSHA256:uploaded.FileSHA256,FileEncSHA256:uploaded.FileEncSHA256,FileLength:proto.Uint64(uint64(len(attachment))),Mimetype:proto.String(body.MIME),Caption:proto.String(body.Message),FileName:proto.String(filepath.Base(body.FileName))}}
  }
 }
 id,err=b.send(ctx,target,message)
 if err!=nil{b.db.Exec(`UPDATE text_outbox SET status='unconfirmed' WHERE key=?`,key);reply(409,map[string]string{"error":"Delivery unconfirmed; do not retry"});return}
 if b.setPresence!=nil{
  _ = b.setPresence(ctx,target,types.ChatPresencePaused,types.ChatPresenceMediaText)
 }
 if action=="edit"{id=body.MessageID}
 if _,err=b.db.Exec(`UPDATE text_outbox SET status='sent',message_id=? WHERE key=?`,id,key);err!=nil{reply(409,map[string]string{"error":"Delivery receipt unconfirmed; do not retry"});return}
 reply(200,map[string]string{"messageId":id})
}

func(b *textBridge)receive(ctx context.Context,socket *wa.Client,event *events.Message){
 if event.Info.IsFromMe || event.Message==nil || !b.ready(){return}
 sender:=event.Info.Sender.ToNonAD();chat:=event.Info.Chat.ToNonAD()
 if !event.Info.IsGroup && chat.Server!="s.whatsapp.net" && chat.Server!="lid"{return}
 if sender.Server=="lid"{resolved,err:=socket.Store.LIDs.GetPNForLID(ctx,sender);if err!=nil || resolved.User==""{return};sender=resolved.ToNonAD()}
 if !event.Info.IsGroup && chat.Server=="s.whatsapp.net" && chat.User!=sender.User{return}
 if !event.Info.IsGroup && chat.Server=="lid"{resolved,err:=socket.Store.LIDs.GetPNForLID(ctx,chat);if err!=nil || resolved.User!=sender.User{return}}
 packet:=textPacket{ChatID:chat.String(),WireChat:chat.String(),SenderID:sender.User,SenderName:event.Info.PushName,MessageID:string(event.Info.ID),IsGroup:event.Info.IsGroup,
  BotIDs:[]string{socket.Store.GetJID().ToNonAD().String(),socket.Store.GetLID().ToNonAD().String()}}
 if !packet.IsGroup{packet.ChatID=sender.User+"@s.whatsapp.net"}
 route:=b.route(packet);if route==""{return}
 m:=event.Message
 packet.Body=m.GetConversation()
 var info *waE2E.ContextInfo
 if text:=m.GetExtendedTextMessage();text!=nil{packet.Body=text.GetText();info=text.GetContextInfo()}
 var media wa.DownloadableMessage;var size uint64
 if image:=m.GetImageMessage();image!=nil{packet.Body=image.GetCaption();packet.MediaType="image";packet.MIME=image.GetMimetype();info=image.GetContextInfo();media=image;size=image.GetFileLength()}
 if doc:=m.GetDocumentMessage();doc!=nil{packet.Body=doc.GetCaption();packet.MediaType="document";packet.MIME=doc.GetMimetype();info=doc.GetContextInfo();media=doc;size=doc.GetFileLength()}
 if audio:=m.GetAudioMessage();audio!=nil{packet.MediaType="audio";packet.MIME=audio.GetMimetype();info=audio.GetContextInfo();media=audio;size=audio.GetFileLength()}
 if info!=nil{
  packet.MentionedIDs=info.GetMentionedJID();packet.QuotedParticipant=info.GetParticipant();packet.QuotedMessageID=info.GetStanzaID();packet.HasQuotedMessage=packet.QuotedMessageID!=""
  quoted:=info.GetQuotedMessage();if quoted!=nil{packet.QuotedText=quoted.GetConversation();if t:=quoted.GetExtendedTextMessage();t!=nil{packet.QuotedText=t.GetText()}}
 }
 if packet.IsGroup{
  // Admission precedes downloading untrusted media or retaining team messages.
  addressed:=regexp.MustCompile(`(?i)\bleo\b`).MatchString(packet.Body)
  for _,bot:=range packet.BotIDs{if packet.QuotedParticipant==bot{addressed=true};for _,id:=range packet.MentionedIDs{if id==bot{addressed=true}}}
  if !addressed{return}
 }
 if media!=nil{
  if size>16*1024*1024{packet.Body+="\n[Attachment exceeds the 16 MB chat limit; its contents are unavailable.]"}else{
   downloadCtx,cancel:=context.WithTimeout(ctx,20*time.Second)
   data,err:=socket.Download(downloadCtx,media);cancel()
   if err==nil && len(data)<=16*1024*1024{
    digest:=sha256.Sum256([]byte(packet.ChatID+"/"+packet.MessageID));extension:=".bin"
    if extensions,_:=mime.ExtensionsByType(packet.MIME);len(extensions)>0{extension=extensions[0]}
    path:=filepath.Join("/data/text/media",route,hex.EncodeToString(digest[:])+extension)
    if os.MkdirAll(filepath.Dir(path),0700)==nil && os.WriteFile(path,data,0600)==nil{packet.HasMedia=true;packet.MediaURLs=[]string{path}}
   }
   if !packet.HasMedia{packet.Body+="\n[Attachment could not be downloaded; its contents are unavailable.]"}
  }
 }
 if packet.Body=="" && !packet.HasMedia{return}
 b.queue(packet,event.Info.Timestamp)
}

func installTextBridge(ctx context.Context,socket *wa.Client,keyfile string)error{
 if getEnv("TEXT_CHAT_ENABLED","LEO_TEXT_CHAT_ENABLED")!="true"{return nil}
 key, err := os.ReadFile(keyfile)
 if err != nil {
  for i := 0; i < 20; i++ {
   time.Sleep(500 * time.Millisecond)
   key, err = os.ReadFile(keyfile)
   if err == nil && len(strings.TrimSpace(string(key))) >= 32 {
    break
   }
  }
 }
 if err != nil || len(strings.TrimSpace(string(key))) < 32 {
  if envKey := strings.TrimSpace(getEnv("VOICE_KEY","LEO_VOICE_KEY","WHATSAPP_SETUP_KEY","HERMES_API_KEY")); len(envKey) >= 32 {
   key = []byte(envKey)
   _ = os.WriteFile(keyfile, key, 0660)
  } else {
   token := make([]byte, 32)
   _, _ = rand.Read(token)
   genKey := hex.EncodeToString(token)
   _ = os.WriteFile(keyfile, []byte(genKey), 0660)
   key = []byte(genKey)
  }
 }
 enabledMarker:=getEnvDefault("/data/remote-engine-enabled","ENGINE_ENABLED_FILE","LEO_ENGINE_ENABLED_FILE")
 bridge,err:=newTextBridge("/data/text.db",strings.TrimSpace(string(key)),getEnv("WHATSAPP_GROUP","LEO_WHATSAPP_GROUP"),enabledMarker,
  func(ctx context.Context,target types.JID,message *waE2E.Message)(string,error){response,err:=socket.SendMessage(ctx,target,message);return string(response.ID),err})
 if err!=nil{return fmt.Errorf("text initialization: %w",err)}
 bridge.upload=socket.Upload
 bridge.setPresence=socket.SendChatPresence
 http.HandleFunc("/text/",bridge.serve)
 downloads:=make(chan struct{},2)
 socket.AddEventHandler(func(value any){
  if event,ok:=value.(*events.Message);ok{
   go func(){select{case downloads<-struct{}{}:case <-ctx.Done():return};defer func(){<-downloads}();bridge.receive(ctx,socket,event)}()
  }
 })
 go func(){<-ctx.Done();bridge.db.Close()}()
 log.Print("Leo text transport initialized on the existing WhatsApp device")
 return nil
}
