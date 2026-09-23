import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Bot,
  FileText,
  FolderOpen,
  Menu,
  Mic,
  Paperclip,
  Plus,
  Send,
  Settings,
  Sparkles,
  User,
  X,
  BookOpen,
  MessageSquare,
  Trash2,
  ChevronRight
} from 'lucide-react';

import './style.css';
import { initAuth, authHeaders } from './auth';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type Msg = {
  role: 'user' | 'assistant';
  content: string;
  citations?: any[];
};

function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [cid, setCid] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [docs, setDocs] = useState<any[]>([]);
  const [user, setUser] = useState<any>();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const media = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  useEffect(() => {
    initAuth()
      .then(() =>
        fetch(API + '/api/v1/me', {
          headers: authHeaders()
        })
      )
      .then((r) => r.json())
      .then(setUser)
      .catch(() => {});

    fetch(API + '/api/v1/documents', {
      headers: authHeaders()
    })
      .then((r) => r.json())
      .then(setDocs)
      .catch(() => {});
  }, []);

  async function send(text = input, speak = false) {
    if (!text.trim() || busy) return;

    setInput('');

    setMessages((m) => [
      ...m,
      {
        role: 'user',
        content: text
      }
    ]);

    setBusy(true);

    try {
      const r = await fetch(API + '/api/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders()
        },
        body: JSON.stringify({
          message: text,
          conversation_id: cid,
          use_knowledge: true
        })
      });

      const d = await r.json();

      if (!r.ok) {
        throw new Error(d.detail || 'Request failed');
      }

      setCid(d.conversation_id);

      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content: d.answer,
          citations: d.citations
        }
      ]);

      if (speak) {
        try {
          const ar = await fetch(API + '/api/v1/voice/synthesize', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...authHeaders()
            },
            body: JSON.stringify({
              text: d.answer
            })
          });

          if (ar.ok) {
            const audio = new Audio(
              URL.createObjectURL(await ar.blob())
            );

            await audio.play();
          }
        } catch (_) {}
      }
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content: 'Une erreur est survenue : ' + e.message
        }
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function startRec() {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: true
    });

    const rec = new MediaRecorder(stream);

    media.current = rec;
    chunks.current = [];

    rec.ondataavailable = (e) => {
      chunks.current.push(e.data);
    };

    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());

      const blob = new Blob(chunks.current, {
        type: 'audio/webm'
      });

      const fd = new FormData();
      fd.append('file', blob, 'voice.webm');

      setBusy(true);

      try {
        const r = await fetch(API + '/api/v1/voice/transcribe', {
          method: 'POST',
          headers: authHeaders(),
          body: fd
        });

        const d = await r.json();

        if (d.text) {
          setBusy(false);
          await send(d.text, true);
        }
      } finally {
        setBusy(false);
      }
    };

    rec.start();
    setRecording(true);
  }

  function stopRec() {
    media.current?.stop();
    setRecording(false);
  }

  async function upload(e: any) {
    const file = e.target.files?.[0];

    if (!file) return;

    const fd = new FormData();
    fd.append('file', file);

    const r = await fetch(API + '/api/v1/documents', {
      method: 'POST',
      headers: authHeaders(),
      body: fd
    });

    const d = await r.json();

    if (r.ok) {
      setDocs((x) => [d, ...x]);
    } else {
      alert(d.detail || 'Upload failed');
    }
  }

  function newChat() {
    setMessages([]);
    setCid(undefined);
    setInput('');
    setSidebarOpen(false);
  }

  const firstName =
    user?.name?.split(' ')[0] || 'Employee';

  return (
    <div className="app-shell">

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* SIDEBAR */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>

        <div className="sidebar-top">

          <div className="brand">
            <div className="brand-logo">
              <Sparkles size={21} />
            </div>

            <div>
              <div className="brand-name">
                SOCAD'EL AI
              </div>

              <div className="brand-subtitle">
                Intelligent Workplace
              </div>
            </div>
          </div>

          <button
            className="mobile-close"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={20} />
          </button>

        </div>

        <button
          className="new-chat-button"
          onClick={newChat}
        >
          <Plus size={19} />
          <span>New conversation</span>
        </button>

        <div className="sidebar-section">

          <div className="section-title">
            <span>Workspace</span>
          </div>

          <button className="sidebar-item active">
            <MessageSquare size={18} />
            <span>Assistant</span>
          </button>

          <button className="sidebar-item">
            <BookOpen size={18} />
            <span>Knowledge base</span>
          </button>

          <button className="sidebar-item">
            <FolderOpen size={18} />
            <span>Documents</span>
          </button>

        </div>

        <div className="sidebar-section knowledge-section">

          <div className="section-title">
            <span>Knowledge</span>

            <label className="add-document">
              <Plus size={15} />
              <input
                type="file"
                hidden
                accept=".pdf,.docx,.txt,.md,.csv,.json"
                onChange={upload}
              />
            </label>
          </div>

          <div className="documents">

            {docs.length === 0 && (
              <div className="empty-documents">
                <FileText size={18} />
                <span>No documents yet</span>
              </div>
            )}

            {docs.slice(0, 8).map((d) => (
              <div className="document-item" key={d.id}>
                <div className="document-icon">
                  <FileText size={15} />
                </div>

                <span title={d.filename}>
                  {d.filename}
                </span>
              </div>
            ))}

          </div>

        </div>

        <div className="sidebar-bottom">

          <button className="sidebar-item">
            <Settings size={18} />
            <span>Settings</span>
          </button>

          <div className="profile">

            <div className="profile-avatar">
              {user?.name
                ? user.name.charAt(0).toUpperCase()
                : 'U'}
            </div>

            <div className="profile-info">
              <strong>{user?.name || 'Employee'}</strong>
              <span>
                {user?.department || 'Internal User'}
              </span>
            </div>

            <ChevronRight size={16} />

          </div>

        </div>

      </aside>

      {/* MAIN */}
      <main className="main">

        {/* HEADER */}
        <header className="topbar">

          <div className="topbar-left">

            <button
              className="menu-button"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={21} />
            </button>

            <div className="mobile-brand">
              <div className="mobile-brand-logo">
                <Sparkles size={17} />
              </div>

              <strong>SOCAD'EL AI</strong>
            </div>

          </div>

          <div className="topbar-status">
            <span className="online-dot" />
            <span>AI Assistant</span>
          </div>

          <div className="topbar-user">
            <div className="topbar-avatar">
              {user?.name
                ? user.name.charAt(0).toUpperCase()
                : 'U'}
            </div>
          </div>

        </header>

        {/* CHAT */}
        <section className="chat-area">

          {messages.length === 0 ? (

            <div className="welcome">

              <div className="welcome-icon">
                <Bot size={36} />
                <span className="sparkle-small">
                  <Sparkles size={15} />
                </span>
              </div>

              <div className="welcome-badge">
                <span className="online-dot" />
                SOCAD'EL AI is ready
              </div>

              <h1>
                Bonjour {firstName}
                <span> 👋</span>
              </h1>

              <p className="welcome-description">
                Votre assistant intelligent pour accéder aux
                connaissances, procédures et documents de
                votre organisation.
              </p>

              <div className="suggestions">

                <button
                  onClick={() =>
                    send(
                      'Quelles sont les principales procédures disponibles ?'
                    )
                  }
                >
                  <BookOpen size={19} />

                  <div>
                    <strong>Explorer les procédures</strong>
                    <span>
                      Rechercher dans la base de connaissances
                    </span>
                  </div>

                  <ChevronRight size={17} />
                </button>

                <button
                  onClick={() =>
                    send(
                      'Comment fonctionne cet assistant ?'
                    )
                  }
                >
                  <Sparkles size={19} />

                  <div>
                    <strong>Découvrir SOCAD'EL AI</strong>
                    <span>
                      Comprendre les capacités de l'assistant
                    </span>
                  </div>

                  <ChevronRight size={17} />
                </button>

                <button
                  onClick={() =>
                    send(
                      'Quels documents sont actuellement disponibles ?'
                    )
                  }
                >
                  <FileText size={19} />

                  <div>
                    <strong>Rechercher un document</strong>
                    <span>
                      Consulter les documents disponibles
                    </span>
                  </div>

                  <ChevronRight size={17} />
                </button>

              </div>

            </div>

          ) : (

            <div className="messages">

              {messages.map((m, i) => (

                <div
                  className={`message-row ${m.role}`}
                  key={i}
                >

                  {m.role === 'assistant' && (
                    <div className="message-avatar ai-avatar">
                      <Sparkles size={17} />
                    </div>
                  )}

                  <div className="message-content">

                    <div className="message-label">
                      {m.role === 'assistant'
                        ? 'SOCAD\'EL AI'
                        : 'You'}
                    </div>

                    <div className="message-bubble">
                      {m.content}

                      {m.citations &&
                        m.citations.length > 0 && (

                          <div className="sources">

                            <div className="sources-title">
                              <FileText size={14} />
                              Sources
                            </div>

                            {m.citations.map(
                              (c: any, j: number) => (

                                <div
                                  className="source-item"
                                  key={j}
                                >
                                  <FileText size={13} />
                                  {c.filename}
                                </div>

                              )
                            )}

                          </div>

                        )}

                    </div>

                  </div>

                  {m.role === 'user' && (
                    <div className="message-avatar user-avatar">
                      <User size={16} />
                    </div>
                  )}

                </div>

              ))}

              {busy && (

                <div className="message-row assistant">

                  <div className="message-avatar ai-avatar">
                    <Sparkles size={17} />
                  </div>

                  <div className="message-content">

                    <div className="message-label">
                      SOCAD'EL AI
                    </div>

                    <div className="message-bubble typing">
                      <span />
                      <span />
                      <span />
                    </div>

                  </div>

                </div>

              )}

            </div>

          )}

        </section>

        {/* COMPOSER */}
        <div className="composer-area">

          <div className="composer">

            <button
              className={`composer-button mic-button ${
                recording ? 'recording' : ''
              }`}
              onClick={
                recording ? stopRec : startRec
              }
              title="Voice input"
            >
              <Mic size={19} />
            </button>

            <button
              className="composer-button attachment-button"
              title="Add document"
            >
              <Paperclip size={18} />
            </button>

            <input
              value={input}
              onChange={(e) =>
                setInput(e.target.value)
              }
              onKeyDown={(e) =>
                e.key === 'Enter' && send()
              }
              placeholder={
                recording
                  ? 'Listening...'
                  : 'Ask SOCAD\'EL AI anything...'
              }
              disabled={recording}
            />

            <button
              className="send-button"
              onClick={() => send()}
              disabled={!input.trim() || busy}
            >
              <Send size={18} />
            </button>

          </div>

          <div className="composer-hint">
            SOCAD'EL AI can make mistakes. Verify
            important information before taking action.
          </div>

        </div>

      </main>

    </div>
  );
}

createRoot(
  document.getElementById('root')!
).render(
  <App />
);