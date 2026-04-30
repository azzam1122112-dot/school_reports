(function () {
  'use strict';

  if (window.__SR_NOTIF_WS_MANAGER_LOADED__) return;
  window.__SR_NOTIF_WS_MANAGER_LOADED__ = true;

  var cfg = window.__srNotifWsConfig || {};
  var enabled = cfg.enabled === true;
  var auth = cfg.authenticated === true;
  var pollUrl = cfg.pollUrl || '/notifications/unread-count/';
  var wsPath = cfg.wsPath || '/ws/notifications/';
  var activeSchoolId = cfg.activeSchoolId == null ? null : Number(cfg.activeSchoolId);
  var debug = cfg.debug === true;
  var tabId = cfg.tabId || ('tab-' + Math.random().toString(36).slice(2));
  var sessionKey = cfg.sessionKey || 'anon';
  var leaderKey = 'sr:notif-ws:leader:' + sessionKey;
  var broadcastName = 'sr-notif-ws:' + sessionKey;
  var leaseMs = Number(cfg.leaseMs || 45000);
  var leaseRenewMs = Number(cfg.leaseRenewMs || 15000);
  var keepaliveMs = Number(cfg.keepaliveMs || 25000);
  var hiddenCloseDelayMs = Number(cfg.hiddenCloseDelayMs || 30000);
  var maxBackoffMs = Number(cfg.maxBackoffMs || 45000);
  var maxReconnectAttempts = Number(cfg.maxReconnectAttempts || 12);
  var retryCooldownMs = Number(cfg.retryCooldownMs || 90000);
  var maxFailuresBeforePoll = Number(cfg.maxFailuresBeforePoll || 6);

  var notifDot = document.getElementById('notifDot');
  var notifDotMobile = document.getElementById('notifDotMobile');
  var circDot = document.getElementById('circDot');
  var circDotMobile = document.getElementById('circDotMobile');
  var hasBadges = !!(notifDot || notifDotMobile || circDot || circDotMobile);

  if (!enabled || !auth || !hasBadges) return;
  if (window.__srNotifWsBootstrapped) return;
  window.__srNotifWsBootstrapped = true;

  var STATE_IDLE = 'idle';
  var STATE_FOLLOWER = 'follower';
  var STATE_CONNECTING = 'connecting';
  var STATE_CONNECTED = 'connected';
  var STATE_RETRY_WAIT = 'retry_wait';
  var STATE_AUTH_BLOCKED = 'auth_blocked';
  var STATE_STOPPED = 'stopped';

  var ws = null;
  var wsTimer = null;
  var wsKeepaliveTimer = null;
  var wsHiddenTimer = null;
  var pollTimerId = null;
  var pollInFlight = false;
  var pollBackoffMs = 60000;
  var wsBackoffMs = 2000;
  var wsFailedConnects = 0;
  var reconnectAttempts = 0;
  var wsCooldownUntil = 0;
  var stoppedByUser = false;
  var wsDisabled = false;
  var wsStartedPollingFallback = false;
  var wsClosingForHidden = false;
  var wsClosingForPageHide = false;
  var currentState = STATE_IDLE;
  var leaderLeaseTimer = null;
  var leaderElectionTimer = null;
  var isLeader = false;
  var bc = null;
  var current = { unread: 0, signatures_pending: 0, count: 0 };

  function log() {
    if (!debug || !window.console || !console.log) return;
    try { console.log.apply(console, ['[WS-Notif]'].concat(Array.prototype.slice.call(arguments))); } catch (e) {}
  }

  function setState(next) {
    if (currentState !== next) {
      log('State:', currentState, '->', next);
      currentState = next;
    }
  }

  function updateDot(dot, n) {
    if (!dot) return;
    var value = Number(n || 0);
    if (value > 0) {
      dot.textContent = String(value);
      dot.style.display = 'flex';
    } else {
      dot.style.display = 'none';
    }
  }

  function applyCounts(data) {
    current.unread = Math.max(0, Number(data.unread || 0));
    current.signatures_pending = Math.max(0, Number(data.signatures_pending || 0));
    current.count = Math.max(0, Number(data.count || 0));
    updateDot(notifDot, current.unread);
    updateDot(notifDotMobile, current.unread);
    updateDot(circDot, current.signatures_pending);
    updateDot(circDotMobile, current.signatures_pending);
  }

  function broadcast(message) {
    if (!bc) return;
    try {
      bc.postMessage(message);
    } catch (e) {}
  }

  function openBroadcastChannel() {
    if (!('BroadcastChannel' in window)) return;
    try {
      bc = new BroadcastChannel(broadcastName);
      bc.onmessage = function (event) {
        var msg = event && event.data;
        if (!msg || msg.tabId === tabId) return;

        if (msg.type === 'counts') {
          applyCounts(msg.payload || {});
          return;
        }
        if (msg.type === 'delta') {
          handleDelta(msg.payload || {}, false);
          return;
        }
        if (msg.type === 'leader-heartbeat' && !isLeader) {
          setState(STATE_FOLLOWER);
          return;
        }
        if (msg.type === 'leader-released') {
          scheduleLeaderElection(250 + Math.floor(Math.random() * 700));
        }
      };
    } catch (e) {
      bc = null;
    }
  }

  function pollClear() {
    if (pollTimerId) {
      clearTimeout(pollTimerId);
      pollTimerId = null;
    }
  }

  function pollSchedule(ms) {
    pollClear();
    pollTimerId = setTimeout(pollTick, Math.max(1000, ms || 60000));
  }

  function pollTick() {
    if (document.hidden || pollInFlight || stoppedByUser) return;
    pollInFlight = true;
    fetch(pollUrl, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('http_' + r.status)); })
      .then(function (data) {
        if (data && data.authenticated === false) {
          wsDisabled = true;
          pollStop();
          wsClearReconnect();
          wsStopKeepalive();
          setState(STATE_AUTH_BLOCKED);
          return;
        }
        applyCounts(data || {});
        broadcast({ type: 'counts', payload: data || {}, tabId: tabId });
        pollBackoffMs = 60000;
        pollSchedule(60000);
      })
      .catch(function () {
        pollBackoffMs = Math.min(Math.round(pollBackoffMs * 1.8), 300000);
        pollSchedule(pollBackoffMs);
      })
      .finally(function () {
        pollInFlight = false;
      });
  }

  function pollStart() {
    if (document.hidden || !isLeader) return;
    pollBackoffMs = 60000;
    pollSchedule(2000);
  }

  function pollStop() {
    pollClear();
  }

  function enablePollingFallback() {
    wsStartedPollingFallback = true;
    if (isLeader) pollStart();
  }

  function wsUrl() {
    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return proto + '://' + location.host + wsPath;
  }

  function isRelevant(sid) {
    if (!activeSchoolId) return true;
    if (sid === null || sid === undefined || sid === '') return true;
    return Number(sid) === Number(activeSchoolId);
  }

  function handleDelta(msg, shouldBroadcast) {
    if (msg.force_resync) {
      try {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'resync' }));
      } catch (e) {}
      return;
    }
    if (!isRelevant(msg.notification_school_id)) return;
    current.unread = Math.max(0, current.unread + Number(msg.delta_unread || 0));
    current.signatures_pending = Math.max(0, current.signatures_pending + Number(msg.delta_signatures_pending || 0));
    current.count = Math.max(0, current.count + Number(msg.delta_count || 0));
    updateDot(notifDot, current.unread);
    updateDot(notifDotMobile, current.unread);
    updateDot(circDot, current.signatures_pending);
    updateDot(circDotMobile, current.signatures_pending);
    if (shouldBroadcast !== false) {
      broadcast({ type: 'delta', payload: msg, tabId: tabId });
    }
  }

  function wsStopKeepalive() {
    if (wsKeepaliveTimer) {
      clearInterval(wsKeepaliveTimer);
      wsKeepaliveTimer = null;
    }
  }

  function wsStartKeepalive() {
    wsStopKeepalive();
    wsKeepaliveTimer = setInterval(function () {
      if (document.hidden || !ws || ws.readyState !== 1) return;
      try { ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {}
    }, keepaliveMs);
  }

  function wsClearReconnect() {
    if (wsTimer) {
      clearTimeout(wsTimer);
      wsTimer = null;
    }
  }

  function wsScheduleReconnect(delayMs) {
    if (!isLeader) return;
    wsClearReconnect();
    wsTimer = setTimeout(function () {
      wsConnect();
    }, Math.max(300, delayMs || 1000));
  }

  function wsCancelHiddenClose() {
    if (wsHiddenTimer) {
      clearTimeout(wsHiddenTimer);
      wsHiddenTimer = null;
    }
    wsClosingForHidden = false;
  }

  function wsScheduleHiddenClose() {
    wsCancelHiddenClose();
    wsHiddenTimer = setTimeout(function () {
      wsHiddenTimer = null;
      if (!document.hidden || !ws || ws.readyState > 1) return;
      wsClosingForHidden = true;
      wsStopKeepalive();
      try { ws.close(1000, 'tab_hidden'); } catch (e) {}
      if (isLeader) {
        releaseLeaderLease();
        isLeader = false;
        setState(STATE_FOLLOWER);
      }
    }, hiddenCloseDelayMs);
  }

  function clearLeaderLeaseTimer() {
    if (leaderLeaseTimer) {
      clearInterval(leaderLeaseTimer);
      leaderLeaseTimer = null;
    }
  }

  function clearLeaderElectionTimer() {
    if (leaderElectionTimer) {
      clearTimeout(leaderElectionTimer);
      leaderElectionTimer = null;
    }
  }

  function readLeaderLease() {
    try {
      var raw = localStorage.getItem(leaderKey);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function writeLeaderLease() {
    if (!isLeader) return;
    try {
      localStorage.setItem(leaderKey, JSON.stringify({
        tabId: tabId,
        expiresAt: Date.now() + leaseMs
      }));
      broadcast({ type: 'leader-heartbeat', tabId: tabId });
    } catch (e) {}
  }

  function releaseLeaderLease() {
    clearLeaderLeaseTimer();
    try {
      var lease = readLeaderLease();
      if (lease && lease.tabId === tabId) {
        localStorage.removeItem(leaderKey);
      }
    } catch (e) {}
    broadcast({ type: 'leader-released', tabId: tabId });
  }

  function becomeFollower() {
    isLeader = false;
    wsCleanup();
    pollStop();
    setState(STATE_FOLLOWER);
  }

  function becomeLeader() {
    isLeader = true;
    writeLeaderLease();
    clearLeaderLeaseTimer();
    leaderLeaseTimer = setInterval(writeLeaderLease, leaseRenewMs);
    setState(STATE_IDLE);
    wsConnect();
  }

  function tryClaimLeadership() {
    if (stoppedByUser || wsDisabled) return;
    if (document.hidden) {
      becomeFollower();
      return;
    }

    var lease = readLeaderLease();
    var now = Date.now();
    if (lease && lease.tabId !== tabId && Number(lease.expiresAt || 0) > now) {
      becomeFollower();
      return;
    }
    becomeLeader();
  }

  function scheduleLeaderElection(delayMs) {
    clearLeaderElectionTimer();
    leaderElectionTimer = setTimeout(tryClaimLeadership, Math.max(150, delayMs || 400));
  }

  function wsCleanup() {
    wsStopKeepalive();
    wsClearReconnect();
    wsCancelHiddenClose();
    if (ws) {
      try {
        ws.onclose = null;
        ws.close();
      } catch (e) {}
      ws = null;
    }
  }

  function wsConnect() {
    if (!isLeader) {
      setState(STATE_FOLLOWER);
      return;
    }
    if (wsDisabled || stoppedByUser) {
      setState(STATE_STOPPED);
      return;
    }
    if (document.hidden) {
      setState(STATE_IDLE);
      return;
    }
    if (!auth) {
      wsCleanup();
      wsDisabled = true;
      setState(STATE_AUTH_BLOCKED);
      return;
    }

    var nowTs = Date.now();
    if (wsCooldownUntil && nowTs < wsCooldownUntil) {
      setState(STATE_RETRY_WAIT);
      wsScheduleReconnect(wsCooldownUntil - nowTs + 200);
      return;
    }

    wsClosingForPageHide = false;
    wsClosingForHidden = false;
    setState(STATE_CONNECTING);
    wsCleanup();

    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      enablePollingFallback();
      setState(STATE_RETRY_WAIT);
      return;
    }

    ws.onopen = function () {
      wsBackoffMs = 2000;
      wsFailedConnects = 0;
      reconnectAttempts = 0;
      wsCooldownUntil = 0;
      wsStartedPollingFallback = false;
      pollStop();
      wsStartKeepalive();
      setState(STATE_CONNECTED);
      if (activeSchoolId) {
        try {
          ws.send(JSON.stringify({ type: 'set_active_school', active_school_id: activeSchoolId }));
        } catch (e) {}
      }
    };

    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!msg || !msg.type) return;
      if (msg.type === 'counts') {
        applyCounts(msg);
        broadcast({ type: 'counts', payload: msg, tabId: tabId });
        return;
      }
      if (msg.type === 'delta') {
        handleDelta(msg, true);
        return;
      }
    };

    ws.onerror = function () {
      log('WS error event.');
    };

    ws.onclose = function (ev) {
      ws = null;
      wsStopKeepalive();

      if (wsClosingForPageHide || wsClosingForHidden) {
        wsClosingForHidden = false;
        setState(STATE_IDLE);
        return;
      }
      if (!isLeader || wsDisabled || stoppedByUser) {
        setState(STATE_STOPPED);
        return;
      }

      var code = ev && typeof ev.code === 'number' && ev.code > 0 ? ev.code : 1006;
      if (code === 4401 || code === 4403) {
        wsDisabled = true;
        pollStop();
        wsClearReconnect();
        setState(STATE_AUTH_BLOCKED);
        return;
      }
      if (code === 4429 || code === 4409) {
        wsCooldownUntil = Date.now() + retryCooldownMs;
        enablePollingFallback();
        setState(STATE_RETRY_WAIT);
        wsScheduleReconnect(retryCooldownMs);
        return;
      }
      if (code === 1000) {
        setState(STATE_IDLE);
        if (!document.hidden) {
          wsScheduleReconnect(2000 + Math.floor(Math.random() * 2000));
        }
        return;
      }
      if (document.hidden) {
        setState(STATE_IDLE);
        return;
      }

      wsFailedConnects += 1;
      reconnectAttempts += 1;
      if (wsFailedConnects >= maxFailuresBeforePoll || reconnectAttempts >= maxReconnectAttempts) {
        wsCooldownUntil = Date.now() + retryCooldownMs;
        enablePollingFallback();
        setState(STATE_RETRY_WAIT);
        wsScheduleReconnect(retryCooldownMs);
        return;
      }

      setState(STATE_RETRY_WAIT);
      wsBackoffMs = Math.min(Math.round(wsBackoffMs * 2), maxBackoffMs);
      wsScheduleReconnect(wsBackoffMs + Math.floor(Math.random() * 1500));
    };
  }

  function wsStop() {
    stoppedByUser = true;
    releaseLeaderLease();
    isLeader = false;
    wsCleanup();
    pollStop();
    clearLeaderElectionTimer();
    setState(STATE_STOPPED);
  }

  openBroadcastChannel();
  scheduleLeaderElection(Math.floor(Math.random() * 400) + 100);

  window.addEventListener('beforeunload', wsStop);

  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    if ((a.getAttribute('href') || '').trim() === (cfg.logoutUrl || '/logout/')) wsStop();
  }, true);

  window.addEventListener('user-authenticated', function () {
    if (currentState === STATE_AUTH_BLOCKED || currentState === STATE_STOPPED) {
      stoppedByUser = false;
      wsDisabled = false;
      scheduleLeaderElection(150);
    }
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      pollStop();
      wsStopKeepalive();
      if (ws && ws.readyState <= 1) wsScheduleHiddenClose();
      return;
    }

    wsCancelHiddenClose();
    wsClosingForHidden = false;
    if (wsDisabled || stoppedByUser) return;

    if (!isLeader) {
      scheduleLeaderElection(Math.floor(Math.random() * 1500) + 300);
      return;
    }

    if (ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify({ type: 'resync' })); } catch (e) {}
      wsStartKeepalive();
      return;
    }
    scheduleLeaderElection(Math.floor(Math.random() * 1000) + 200);
  }, { passive: true });

  window.addEventListener('pagehide', function () {
    wsClosingForPageHide = true;
    wsClearReconnect();
    wsStopKeepalive();
    wsCancelHiddenClose();
    if (ws) {
      try { ws.close(1000, 'pagehide'); } catch (e) {}
      ws = null;
    }
    if (isLeader) releaseLeaderLease();
  }, { passive: true });

  window.addEventListener('pageshow', function () {
    wsClosingForPageHide = false;
    if (document.hidden || wsDisabled || stoppedByUser) return;
    scheduleLeaderElection(Math.floor(Math.random() * 1000) + 250);
  }, { passive: true });

  window.addEventListener('online', function () {
    if (document.hidden || wsDisabled || stoppedByUser) return;
    if (isLeader && ws && ws.readyState === 1) {
      try { ws.send(JSON.stringify({ type: 'resync' })); } catch (e) {}
      return;
    }
    scheduleLeaderElection(Math.floor(Math.random() * 700) + 300);
  }, { passive: true });

  window.addEventListener('storage', function (event) {
    if (!event || event.key !== leaderKey || stoppedByUser || wsDisabled) return;
    var lease = readLeaderLease();
    if (lease && lease.tabId !== tabId && Number(lease.expiresAt || 0) > Date.now()) {
      if (isLeader) {
        becomeFollower();
      } else {
        setState(STATE_FOLLOWER);
      }
      return;
    }
    if (!document.hidden) {
      scheduleLeaderElection(Math.floor(Math.random() * 500) + 200);
    }
  });

  window.__srNotifWs = {
    stop: wsStop,
    state: function () { return currentState; },
    isLeader: function () { return isLeader; },
    resync: function () {
      if (isLeader && ws && ws.readyState === 1) {
        try { ws.send(JSON.stringify({ type: 'resync' })); } catch (e) {}
      }
    }
  };
})();
