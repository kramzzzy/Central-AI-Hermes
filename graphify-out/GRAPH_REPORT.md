# Graph Report - Central-AI-Hermes  (2026-10-15)

## Corpus Check
- 144 files · ~100,329 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 12 file(s) not represented in the graph (top: (none) 5, .patch 2, .example 1)

## Summary
- 1750 nodes · 4545 edges · 86 communities (61 shown, 25 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 327 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a50ac3a2`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- BackgroundTasks
- NativeChat
- production-bootstrap.mjs
- .do_POST
- build_engine_adapter
- main.go
- os
- pathlib
- FishCall
- local-bridge.py
- testing.T
- BackupError
- GoogleWorkspace
- whatsapp-call-voice.py
- coolify_voice.py
- hermes_google.py
- answerIncomingWithVoice
- os-backup.py
- hermes-os-mcp.py
- StreamTests
- main
- context.Context
- relayFanout
- json
- re
- VoiceTests
- textBridge
- .test_resume_is_read_only_and_does_not_reload_history
- OSCalls
- stream_conversation.py
- StreamPhoneCall
- hermes-chat-entry.py
- Reader
- phone_cloud_asr.py
- routing
- wireTaskCallback
- SpecialistPool
- AGENTS.md
- VoiceProvider
- BridgeTests
- response
- .do_POST
- NativeSpeech
- hermes-catalog.py
- worker.ts
- hermes_laya.py
- TurnControl
- StreamingTurn
- FreeSpeech
- StreamingSpeech
- whatsapp_stream.py
- resolve_caller
- routing.go
- SpeculativeReply
- claimTaskCallbackUsing
- whatsapp_system_context.py
- Central AI — Hermes Intelligence & Superpowers Stack (Box 1)
- first-run-ui.js
- SetupError
- StreamingTurn
- laya-router-service.py
- Handler
- run
- .prepare
- Follow-up tracker
- Meeting preparation
- Morning briefing
- Personal assistant booking
- Receipts and expenses
- Team coordination
- WidgetWeatherTests
- Handler
- Production Build Recipes & Deployment Architecture
- .connectFilter
- inspect_workspace
- ConversationModelTests
- TextModelProfileTests
- TextSupervisor
- register
- send_message
- serve
- .test_canceled_event_batch_cannot_emit_or_finish_a_reply
- CallbackIntentTests
- complete
- PlaybackTests
- IdleThread

## God Nodes (most connected - your core abstractions)
1. `NativeChat` - 45 edges
2. `BackgroundTasks` - 40 edges
3. `FishCall` - 30 edges
4. `main()` - 29 edges
5. `voiceAudio` - 25 edges
6. `atomic_write()` - 24 edges
7. `GoogleWorkspace` - 24 edges
8. `routing()` - 24 edges
9. `StreamPhoneCall` - 24 edges
10. `TurnControl` - 23 edges

## Surprising Connections (you probably didn't know these)
- `Assistant Identity & Voice Call Architecture` --references--> `call_whatsapp_contact()`  [INFERRED]
  AGENTS.md → scripts/hermes-os-mcp.py
- `_build_message_event()` --calls--> `get_whatsapp_reply_style()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/whatsapp_system_context.py
- `get_live_weather()` --calls--> `read_weather()`  [INFERRED]
  hermes_plugins/central_ai_app_os/client.py → scripts/app_os_weather.py
- `run_route()` --calls--> `build_engine_adapter()`  [EXTRACTED]
  scripts/whatsapp-text-engine.py → hermes_plugins/central_ai_whatsapp/adapter.py
- `connect()` --indirect_call--> `route()`  [INFERRED]
  hermes_plugins/central_ai_whatsapp/adapter.py → scripts/laya_embedding_service.py

## Import Cycles
- None detected.

## Communities (86 total, 25 thin omitted)

### Community 0 - "BackgroundTasks"
Cohesion: 0.07
Nodes (9): NativeTaskBridge, speech_model(), TaskTests, factory(), CallbackTests, FishService, start(), BackgroundTasks (+1 more)

### Community 1 - "NativeChat"
Cohesion: 0.07
Nodes (13): NativeChat, Installation, CallTaskChat, delivery_time(), next_time(), RoutineError, Routines, check_account() (+5 more)

### Community 2 - "production-bootstrap.mjs"
Cohesion: 0.06
Nodes (52): databaseSettings(), field(), httpsOrigin(), modelSettings(), providers, SetupError, verifyProvider(), checkDatabase() (+44 more)

### Community 3 - ".do_POST"
Cohesion: 0.06
Nodes (23): RunProcesses, ExportError, progress(), MemoryControls, MemoryError, revision(), read_bank(), at() (+15 more)

### Community 4 - "build_engine_adapter"
Cohesion: 0.05
Nodes (36): build_engine_adapter(), _build_message_event(), _complete(), connect(), _dispatch_inline_reply(), edit_message(), on_processing_complete(), _poll_messages() (+28 more)

### Community 5 - "main.go"
Cohesion: 0.08
Nodes (5): TestIncomingRelayProbeDoesNotEndorseUnavailableEndpoint(), ptr(), claimedCount(), TestTextAdmissionAndRouting(), callProgressWriter

### Community 6 - "os"
Cohesion: 0.07
Nodes (22): install_files(), installed(), profile_home(), profile_lock(), identity_text(), rename_profile(), atomic_write(), profile_lock() (+14 more)

### Community 8 - "FishCall"
Cohesion: 0.09
Nodes (6): PhoneAudioFilter, SpeechGate, handle(), FeedTests, GateTests, FishCall

### Community 9 - "local-bridge.py"
Cohesion: 0.09
Nodes (11): integration_config(), managed_environment(), memory_config(), sync_profile(), export_authorized(), read_library(), authorized(), complete() (+3 more)

### Community 10 - "testing.T"
Cohesion: 0.11
Nodes (31): TestContinuousFilterWireAndReconnectKeepCallAlive(), TestFilterRequiresExactCallCredentials(), conversationFixture(), eventually(), TestBargeInDropsOldAudioAndKeepsOpeningWords(), TestContinuousReceiveCarriesAudioWhileReplyIsBusy(), TestFailureKeepsCallAliveAndNextTurnWorksWithoutReplay(), TestInterruptMergesOnlyUnsubmittedUtterances() (+23 more)

### Community 11 - "BackupError"
Cohesion: 0.10
Nodes (14): ArtworkError, available(), _download_image_to_b64(), generate(), _generate_procedural_avatar(), _NoRedirect, BackupError, Backups (+6 more)

### Community 12 - "GoogleWorkspace"
Cohesion: 0.18
Nodes (13): extract_email_content(), extract_parts(), GoogleError, GoogleWorkspace, fetch_single(), page(), identifier(), identity() (+5 more)

### Community 13 - "whatsapp-call-voice.py"
Cohesion: 0.07
Nodes (4): BackupError, handle_backup(), pcm(), tts_config()

### Community 14 - "coolify_voice.py"
Cohesion: 0.17
Nodes (25): atomic_write(), apply(), config(), configured(), deployment_phase(), deployment_status(), fingerprint(), NoRedirect (+17 more)

### Community 15 - "hermes_google.py"
Cohesion: 0.11
Nodes (4): read_marker(), restore_installation(), preserved_profiles(), context_name()

### Community 16 - "answerIncomingWithVoice"
Cohesion: 0.09
Nodes (9): answerIncoming(), answerIncomingWithVoice(), TestIncomingAnswersBeforeSlowVoiceAndRetainsEarlyReady(), TestIncomingHangupCancelsPendingVoicePreparation(), readOutgoingRequest(), fakeIncoming, incomingHandle, outgoingRequest (+1 more)

### Community 17 - "os-backup.py"
Cohesion: 0.13
Nodes (12): native_export(), task_file_context(), context(), command(), export(), filehash(), recovery(), verify() (+4 more)

### Community 18 - "hermes-os-mcp.py"
Cohesion: 0.11
Nodes (15): add_knowledge(), call_app_os_api(), close_widget(), control_widget(), delegate_specialists(), end_call(), get_knowledge(), get_live_weather() (+7 more)

### Community 19 - "StreamTests"
Cohesion: 0.11
Nodes (3): CallbackHTTPTests, StreamTests, pending()

### Community 20 - "main"
Cohesion: 0.12
Nodes (20): engineHealthy(), TestEngineCannotConnectBeforeExplicitEnableMarker(), TestEngineHealthRequiresActualWhatsAppConnection(), waitEngineEnabled(), groupsHandler(), admittedCaller(), main(), connectPairedDevice() (+12 more)

### Community 21 - "context.Context"
Cohesion: 0.16
Nodes (10): wireAudio(), probeAudio(), wireProbe(), voiceAudio, prepareVoice(), prepareVoiceFor(), prepareVoiceInternal(), prepareVoiceOutbound() (+2 more)

### Community 22 - "relayFanout"
Cohesion: 0.12
Nodes (11): newRelayFanout(), newRtpReplayFilter(), newFakeRelay(), TestRelayFanoutMigratesReceivesAndUnblocksOnClose(), TestRelayFanoutRefreshesOnlyMatchingAllocateAndBroadcastsMedia(), TestRtpReplayAcrossRelaysPreservesAudioPlayout(), fakeRelay, relayFanout (+3 more)

### Community 23 - "json"
Cohesion: 0.18
Nodes (15): call_contact(), close_widget(), control_widget(), end_call(), get_live_weather(), inspect_workspace(), inspection_available(), navigate_browser() (+7 more)

### Community 24 - "re"
Cohesion: 0.12
Nodes (14): call_app_os_mcp(), call_whatsapp_action(), fish_api(), is_casual_greeting_intent(), is_correction(), is_goodbye_intent(), is_task_cancellation(), resolve_contact_target() (+6 more)

### Community 26 - "textBridge"
Cohesion: 0.14
Nodes (7): newTextBridge(), TestTextReplyBindingAndSingleDelivery(), TestTextUncertainDeliveryCannotRetryAndClaimCannotReplay(), textFixture(), textRequest(), textBridge, textPacket

### Community 27 - ".test_resume_is_read_only_and_does_not_reload_history"
Cohesion: 0.09
Nodes (3): NativeWarmupTests, owned_class(), TextBoundaryTests

### Community 28 - "OSCalls"
Cohesion: 0.14
Nodes (5): OSCalls, dispatch(), reconcile(), OSNativeTaskBridge, NativeVoiceConversation

### Community 29 - "stream_conversation.py"
Cohesion: 0.14
Nodes (9): conversation_llm(), call_greeting(), OSStreamVoice, emit(), text(), Conversation, fish_rest_tts(), emit() (+1 more)

### Community 31 - "hermes-chat-entry.py"
Cohesion: 0.12
Nodes (4): install_process_token(), configure_business_phone_runtime(), configure_phone_tools(), voice_call_note()

### Community 33 - "phone_cloud_asr.py"
Cohesion: 0.13
Nodes (3): CloudRecognizer, PreparedRecognition, run()

### Community 34 - "routing"
Cohesion: 0.19
Nodes (8): provision_business_phone(), provision_caller_phone(), provision_phone_profile(), caller(), routing(), text_routes(), quiet_whatsapp_display(), run_route()

### Community 35 - "wireTaskCallback"
Cohesion: 0.20
Nodes (6): TestCallbackDoesNotPreparePrivateReportBeforeActualPeerAnswer(), TestHangupDuringCallbackPreparationCannotAttachLateAudio(), TestRejectedCallbackNoNativeSessionAndOneOutcome(), wireTaskCallback(), callbackHandle, fakeCallback

### Community 37 - "AGENTS.md"
Cohesion: 0.14
Nodes (11): Assistant Identity & Voice Call Architecture, graphify Knowledge Graph & Architecture Navigation, Hindsight Persistent Agent Memory, Mandatory Agent Communication Policy: Minimal, Concise & Precise, Mandatory Box 1 Dual-Push Synchronization (`Central-AI-Hermes`), Strict Global Design Pattern & UI/UX Policy, Strict Prohibition: No Remote / Live Server Access, Supabase & Database Layer Best Practices (RLS & Schema Security) (+3 more)

### Community 38 - "VoiceProvider"
Cohesion: 0.21
Nodes (7): VoiceProvider, authenticate(), relay(), run(), native_enabled(), fixed_speech(), speech()

### Community 40 - "response"
Cohesion: 0.19
Nodes (5): openrouter_stream(), client(), open_request(), Response, response()

### Community 41 - ".do_POST"
Cohesion: 0.18
Nodes (7): restrict_native_specialist_runtime(), current_phone_chat(), incomplete_utterance(), native(), recognize(), stream_recognize(), TurnState

### Community 43 - "hermes-catalog.py"
Cohesion: 0.23
Nodes (4): load_config(), field(), available_tools(), tool_options()

### Community 44 - "worker.ts"
Cohesion: 0.18
Nodes (5): calendarTimer, memoryTimer, pulse(), routinesTimer, timer

### Community 45 - "hermes_laya.py"
Cohesion: 0.27
Nodes (4): api_key(), available(), classify(), configured()

### Community 46 - "TurnControl"
Cohesion: 0.27
Nodes (4): main(), transcribe(), TurnControl, close()

### Community 50 - "whatsapp_stream.py"
Cohesion: 0.22
Nodes (4): TurnCancelled, PhonePCM, output(), output()

### Community 51 - "resolve_caller"
Cohesion: 0.22
Nodes (6): allowed_audio_call(), native_conversation(), private_phone_member(), task_native(), CallerTests, resolve_caller()

### Community 52 - "routing.go"
Cohesion: 0.36
Nodes (10): allowedPeer(), cleanDigits(), configuredRouteAllowed(), configuredTextRoute(), managedCallerAllowed(), managedPhoneRouting(), TestFreshUnconfiguredInstallationAdmitsNobody(), TestManagedContactsBindRoutesAndDenyRemovedCallers() (+2 more)

### Community 54 - "claimTaskCallbackUsing"
Cohesion: 0.31
Nodes (10): callbackControl(), callbackTargetAllowed(), claimTaskCallback(), claimTaskCallbackUsing(), finishTaskCallback(), TestCallbackClaimsAreBoundToThePolledOwner(), TestCallbackConfigurationCannotRedirectToAnotherNumber(), TestCallbackRequiresAuthorizedMarkOrMichaelAndValidClaim() (+2 more)

### Community 55 - "whatsapp_system_context.py"
Cohesion: 0.31
Nodes (7): get_channel_context(), get_configured_assistant_name(), get_whatsapp_reply_style(), install_booking_skill(), _native_health(), owner_channel_context(), _probe()

### Community 56 - "Central AI — Hermes Intelligence & Superpowers Stack (Box 1)"
Cohesion: 0.22
Nodes (8): 🚀 1-Click Deployment on Coolify, 🏗️ Box 1 Architecture, Central AI — Hermes Intelligence & Superpowers Stack (Box 1), 🔒 Security & Decoupling Policy, 📦 Services Included, Step 1: Add Public Repository Resource, Step 2: Configure Environment Variables, Step 3: Click Deploy

### Community 57 - "first-run-ui.js"
Cohesion: 0.36
Nodes (6): api(), authDisplay(), install(), models, pollDeployment(), show()

### Community 58 - "SetupError"
Cohesion: 0.31
Nodes (3): NoRedirect, SetupError, WhatsAppSetup

### Community 62 - "run"
Cohesion: 0.33
Nodes (5): filter_playback_input(), normalized(), words(), run(), confirm()

### Community 64 - "Follow-up tracker"
Cohesion: 0.29
Nodes (6): Follow-up tracker, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 65 - "Meeting preparation"
Cohesion: 0.29
Nodes (6): Meeting preparation, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 66 - "Morning briefing"
Cohesion: 0.29
Nodes (6): Morning briefing, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 67 - "Personal assistant booking"
Cohesion: 0.29
Nodes (6): Personal assistant booking, Pitfalls, Prerequisites, Procedure, Verification, When to Use

### Community 68 - "Receipts and expenses"
Cohesion: 0.29
Nodes (6): Pitfalls, Prerequisites, Procedure, Receipts and expenses, Verification, When to Use

### Community 69 - "Team coordination"
Cohesion: 0.29
Nodes (6): Pitfalls, Prerequisites, Procedure, Team coordination, Verification, When to Use

### Community 71 - "Handler"
Cohesion: 0.33
Nodes (3): connected(), finish(), Handler

### Community 72 - "Production Build Recipes & Deployment Architecture"
Cohesion: 0.40
Nodes (4): 3-Way Installation Sequence, Existing Box 1 installations: preserve data before the storage fix, Networking & Resolution, Production Build Recipes & Deployment Architecture

### Community 74 - "inspect_workspace"
Cohesion: 0.40
Nodes (3): inspect_from_chat(), inspect_workspace(), validate_inspection()

### Community 78 - "register"
Cohesion: 0.50
Nodes (3): dashboard_origin(), register(), status()

### Community 80 - "serve"
Cohesion: 0.67
Nodes (3): serve(), do_POST(), reply()

### Community 83 - "complete"
Cohesion: 0.67
Nodes (3): complete(), _build_responses_kwargs(), wire()

## Knowledge Gaps
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `BackgroundTasks` connect `BackgroundTasks` to `pathlib`, `hermes_google.py`, `StreamTests`, `re`, `OSCalls`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Should `BackgroundTasks` be split into smaller, more focused modules?**
  _Cohesion score 0.06553041434028799 - nodes in this community are weakly interconnected._
- **Why does `VoiceTests` connect `VoiceTests` to `pathlib`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **Should `NativeChat` be split into smaller, more focused modules?**
  _Cohesion score 0.07087719298245614 - nodes in this community are weakly interconnected._
- **Why does `NativeChat` connect `NativeChat` to `local-bridge.py`, `SpecialistPool`, `whatsapp-call-voice.py`, `hermes_google.py`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Should `production-bootstrap.mjs` be split into smaller, more focused modules?**
  _Cohesion score 0.057297297297297295 - nodes in this community are weakly interconnected._
- **Should `.do_POST` be split into smaller, more focused modules?**
  _Cohesion score 0.06153846153846154 - nodes in this community are weakly interconnected._