/**
 * Shared helpers for the ShipSense web UI (loaded by both pages).
 *
 * SSE protocol (must match server/app.py):
 *   Requests go to POST /api/run with JSON body {"cmd": ..., "orders": ...}.
 *   Each SSE frame is "data: <JSON>\n\n" where <JSON> is one of:
 *     {"event": "log",      "text": "<output line>"}
 *     {"event": "complete", "code": <int exit code>}
 *   A stream always ends with exactly one "complete" event.
 */

/** Escape a string for safe interpolation into HTML markup/attributes. */
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** Headers for /api/* calls; forwards an optional dashboard auth token. */
function apiHeaders(extra) {
    const headers = Object.assign({}, extra);
    let token = null;
    try {
        token = window.localStorage.getItem('dashboard_token');
    } catch (e) {
        // localStorage unavailable (e.g. blocked); proceed without a token
    }
    if (token) headers['X-Dashboard-Token'] = token;
    return headers;
}

/**
 * Run a command via POST /api/run and consume the SSE stream.
 *
 * Buffers TCP chunks properly: decodes with {stream: true} so multibyte
 * characters split across chunks survive, accumulates into a string buffer,
 * splits complete events on "\n\n", and keeps the trailing partial segment
 * in the buffer for the next chunk.
 *
 * @param {string} cmd - command name (e.g. "amazon", "print-order X")
 * @param {string|null} orders - optional comma-separated order IDs
 * @param {{onLog: function(string), onComplete: function(number)}} handlers
 *   onComplete is guaranteed to be called exactly once, with the process
 *   exit code (or -1 if the stream ended/errored without a complete event).
 */
async function runCommandStream(cmd, orders, handlers) {
    const onLog = handlers.onLog || function () {};
    const onComplete = handlers.onComplete || function () {};
    let completed = false;

    const finish = (code) => {
        if (!completed) {
            completed = true;
            onComplete(code);
        }
    };

    const handleEvent = (rawEvent) => {
        // An SSE event may contain multiple lines; we only emit single
        // "data: ..." lines from the server.
        for (const line of rawEvent.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            let data;
            try {
                data = JSON.parse(line.substring(6));
            } catch (e) {
                console.error('Bad SSE payload:', line);
                continue;
            }
            if (data.event === 'complete') {
                finish(typeof data.code === 'number' ? data.code : -1);
            } else if (data.event === 'log') {
                onLog(data.text || '');
            }
        }
    };

    try {
        const response = await fetch('/api/run', {
            method: 'POST',
            headers: apiHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(orders ? { cmd: cmd, orders: orders } : { cmd: cmd }),
        });

        if (!response.ok || !response.body) {
            let detail = `HTTP ${response.status}`;
            try {
                const err = await response.json();
                if (err.error || err.detail) detail += `: ${err.error || err.detail}`;
            } catch (e) { /* non-JSON error body */ }
            onLog(`Request failed (${detail})`);
            finish(-1);
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const events = buffer.split('\n\n');
            buffer = events.pop(); // keep the last (possibly partial) segment
            for (const event of events) {
                handleEvent(event);
            }
        }
        // Flush any decoder-internal state and trailing buffered event
        buffer += decoder.decode();
        if (buffer.trim()) handleEvent(buffer);

        // Stream ended without a complete event (server died mid-stream)
        finish(-1);
    } catch (e) {
        console.error('SSE stream error:', e);
        onLog('Connection error: ' + e.message);
        finish(-1);
    }
}
