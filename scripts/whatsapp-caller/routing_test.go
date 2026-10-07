package main

import (
 "os"
 "path/filepath"
 "testing"
)

func TestManagedContactsBindRoutesAndDenyRemovedCallers(t *testing.T) {
 previous:=phoneRoutingPath;phoneRoutingPath=filepath.Join(t.TempDir(),"routing.json");t.Cleanup(func(){phoneRoutingPath=previous})
 content:=`{"contacts":[{"number":"447700900123","route":"contact-447700900123","calls":true},{"number":"447700900124","route":"contact-447700900124","calls":false}]}`
 if err:=os.WriteFile(phoneRoutingPath,[]byte(content),0600);err!=nil{t.Fatal(err)}
 if configuredTextRoute("447700900124")!="contact-447700900124" || !configuredRouteAllowed("contact-447700900124"){t.Fatal("team text contact unavailable")}
 if managedCallerAllowed("447700900124") || managedCallerAllowed("639267200480") || configuredTextRoute("639267200480")!="" {t.Fatal("ungranted identity admitted")}
 if !managedCallerAllowed("447700900123") {t.Fatal("configured owner unavailable")}
 if err:=os.WriteFile(phoneRoutingPath,[]byte(`{"contacts":[{"number":"447700900124","route":"../../leo"}]}`),0600);err!=nil{t.Fatal(err)}
 if configuredRouteAllowed("../../leo") || configuredTextRoute("447700900124")!="" {t.Fatal("malformed configuration admitted")}
}

func TestFreshUnconfiguredInstallationAdmitsNobody(t *testing.T) {
 previous:=phoneRoutingPath;phoneRoutingPath=filepath.Join(t.TempDir(),"routing.json");t.Cleanup(func(){phoneRoutingPath=previous})
 if err:=os.WriteFile(phoneRoutingPath,[]byte(`{"contacts":[]}`),0600);err!=nil{t.Fatal(err)}
 t.Setenv("LEO_WHATSAPP_OWNER","");t.Setenv("LEO_WHATSAPP_BUSINESS_CONTACT","")
 if admittedCaller("639267200480", "639267200480")!="" || managedCallerAllowed("639267200480") || configuredTextRoute("639267200480")!="" {t.Fatal("empty installation admitted a legacy contact")}
}
