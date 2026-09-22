# App-to-Extension Communication Failure Report

## 1. Problem Statement
Currently, the Project Sera Extension utilizes two distinct communication channels:
- **App to Extension**: Chrome Native Messaging (via `native_host/host.py` on port 49153).
- **Extension to App**: Direct Local HTTP (`http://127.0.0.1:49152`) for pushing large SDC telemetry and filing datasets.

**The Issue**: The direct local HTTP listener pipeline fails completely whenever the Native Messaging bridge is disrupted or non-functional. 

### Root Cause Analysis
Although `ExtensionListener` (the desktop HTTP server) runs independently as a `QThread`, the failure originates in the Manifest V3 service worker (`background.js`). 
When `chrome.runtime.connectNative(hostName)` executes, if the native host is unregistered, blocked by antivirus, or missing, it causes an unhandled disconnect or exception in the service worker lifecycle. Because the background script's execution context relies on this native port for initialization or heartbeat state, its failure cascades, preventing the extension from successfully executing the `fetch('http://127.0.0.1:49152')` calls to flush the SDC assembler. Thus, the HTTP pipeline is artificially coupled to the stability of the Windows Registry and the Native Messaging `stdio` pipe.

## 2. Proposed Architectural Alternatives

To permanently decouple these systems and provide a stable App-to-Extension pipeline that survives Manifest V3 sleep cycles and bypasses the Windows Registry, the following options are proposed:

### Option A: Local WebSockets (Highly Recommended)
Replace the Native Messaging bridge entirely with a local WebSocket server.

- **Architecture**: The desktop app hosts a WebSocket server (e.g., `ws://127.0.0.1:49154`). The extension's `background.js` establishes a persistent connection.
- **Data Flow**: Full-duplex. The App pushes SCA autofill commands down the socket; the Extension pushes SDC payloads up the socket.
- **MV3 Resilience**: When the service worker goes to sleep, the socket drops. A robust reconnect wrapper in `background.js` instantly re-establishes the connection the moment the browser wakes the worker up (e.g., via tab changes or `chrome.alarms`).
- **Benefits**: Completely removes the dependency on Windows Registry keys and `native_host`. Highly stable, real-time, and standard.

### Option B: Server-Sent Events (SSE) + HTTP POST
Maintain the existing HTTP infrastructure but swap Native Messaging for an SSE stream.

- **Architecture**: The desktop app adds a streaming HTTP endpoint (`http://127.0.0.1:49152/stream`). The extension subscribes using the native browser `EventSource` API.
- **Data Flow**: 
  - *App to Ext*: App yields events (SCA injections) into the SSE stream.
  - *Ext to App*: Extension continues using standard `fetch()` POSTs for SDC flushes.
- **MV3 Resilience**: The `EventSource` is re-initialized whenever the service worker wakes up. 
- **Benefits**: Relies strictly on standard HTTP, perfectly complementing the existing `ExtensionListener`. Unidirectional flow prevents complex socket state management.

### Option C: HTTP Short Polling (The Fallback)
A stateless, polling-based approach.

- **Architecture**: The desktop app exposes a REST endpoint (`/poll_commands`).
- **Data Flow**: The extension uses `chrome.alarms` or tab activity to periodically `fetch` this endpoint, asking for pending autofill commands.
- **Benefits**: Bulletproof stability. It is completely immune to MV3 sleep cycles since it is entirely stateless.
- **Drawbacks**: Introduces minor latency (the polling interval) compared to the instant push of WebSockets or SSE, which might slightly degrade the "instant" feel of ambient clipboard autofill.

## 3. Recommendation
**Option A (WebSockets)** is the optimal path forward. It retains the instant, zero-latency push capability required for Sera Clipboard Assist (SCA) while completely eliminating the brittle Native Messaging `stdio` pipe and registry dependencies.
