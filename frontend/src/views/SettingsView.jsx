import { useEffect, useState } from "react";
import { del, get, post, put } from "../api.js";
import { useApp } from "../context.js";
import Icon from "../icons.jsx";

const MODEL_FIELD = {
  gemini: "gemini.model",
  anthropic: "anthropic.model",
  azure_openai: "azure.chat_deployment",
  bedrock: "aws.chat_model_id",
};
const KEY_FOR = {
  gemini: "GEMINI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  azure_openai: "KK_AZURE__OPENAI_API_KEY",
};
const KEY_HELP = {
  gemini: { text: "Get a free key at Google AI Studio (no credit card). Keys start with “AIza”.", url: "https://aistudio.google.com/apikey" },
  anthropic: { text: "Create a key in the Anthropic Console.", url: "https://console.anthropic.com/settings/keys" },
  azure_openai: { text: "Found under your Azure OpenAI resource → Keys and Endpoint.", url: null },
};
const SOURCE_LABEL = {
  saved: "Saved on this computer",
  environment: "From your environment (e.g. ~/.bashrc)",
  session: "Set for this session only",
  missing: "Not set",
};
const SHORTCUT_LABEL = { windows: "Add to Start menu", macos: "Add to Applications", linux: "Add to app menu" };
const AZURE_FIELDS = [
  ["azure.search_endpoint", "Azure AI Search endpoint", "https://NAME.search.windows.net"],
  ["azure.openai_endpoint", "Azure OpenAI endpoint", "https://NAME.openai.azure.com"],
  ["azure.embedding_deployment", "Embedding deployment", ""],
];
const AWS_FIELDS = [
  ["aws.region", "Region", "us-east-1"],
  ["aws.opensearch_endpoint", "OpenSearch endpoint", "https://ID.REGION.aoss.amazonaws.com"],
];

export default function SettingsView() {
  const { status, refresh, toast, isLocal, busy } = useApp();
  const [settings, setSettings] = useState(null);
  const [values, setValues] = useState({});
  const [provider, setProvider] = useState(status.provider);
  const [keyValue, setKeyValue] = useState("");
  const [remember, setRemember] = useState(true);
  const [test, setTest] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    get("/api/settings")
      .then((s) => {
        setSettings(s);
        setValues(s.values);
        setProvider(s.provider);
      })
      .catch((e) => toast(e.message, "error"));
  }, [toast]);

  if (!settings) return <section className="view"><div className="loading">Loading…</div></section>;

  const llm = values["llm.provider"];
  const keyName = KEY_FOR[llm];
  const keyInfo = status.keys.find((k) => k.name === keyName);
  const set = (k, v) => setValues((old) => ({ ...old, [k]: v }));
  const locked = !isLocal;

  const save = async (partial, message) => {
    setSaving(true);
    try {
      const s = await put("/api/settings", partial);
      setSettings(s);
      setValues(s.values);
      setProvider(s.provider);
      await refresh();
      toast(message, "success");
      return true;
    } catch (e) {
      toast(e.message, "error");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const saveAi = async () => {
    const changed = { "llm.provider": llm };
    if (MODEL_FIELD[llm]) changed[MODEL_FIELD[llm]] = values[MODEL_FIELD[llm]];
    let ok = true;
    if (keyName && keyValue.trim()) ok = await saveKey();
    if (ok) await save({ values: changed }, "AI model settings saved.");
    setTest(null);
  };

  const saveKey = async () => {
    try {
      await post("/api/settings/key", { name: keyName, value: keyValue, remember });
      setKeyValue("");
      await refresh();
      return true;
    } catch (e) {
      toast(e.message, "error");
      return false;
    }
  };

  const forgetKey = async () => {
    try {
      await del(`/api/settings/key/${keyName}`);
      await refresh();
      toast("Saved key removed from this computer.", "success");
    } catch (e) {
      toast(e.message, "error");
    }
  };

  const runTest = async () => {
    setTest({ running: true });
    try {
      setTest(await post("/api/settings/test-llm"));
    } catch (e) {
      setTest({ ok: false, error: e.message });
    }
  };

  const saveBackend = () => {
    const fields = provider === "azure" ? AZURE_FIELDS : provider === "aws" ? AWS_FIELDS : [];
    const changed = Object.fromEntries(fields.map(([k]) => [k, values[k]]));
    save({ provider, values: changed }, `Storage set to ${status.backends.find((b) => b.name === provider)?.label}.`);
  };

  return (
    <section className="view settings">
      <header className="view-head">
        <div>
          <h1>Settings</h1>
          <p className="sub">Connect an AI model for written answers, and choose where the knowledge base is stored.</p>
        </div>
      </header>

      {locked && (
        <div className="note warn">
          <Icon name="alert" size={16} /> Settings can only be changed on the computer running Knowledge Keeper.
        </div>
      )}

      <div className="card">
        <h2>AI model</h2>
        <p className="muted">
          Without one, questions return the best-matching passages. With one, you get written answers with citations and
          deeper analysis. Google Gemini has a free tier.
        </p>
        <div className="grid2">
          <label className="field">
            <span>Provider</span>
            <select value={llm} onChange={(e) => set("llm.provider", e.target.value)} disabled={locked}>
              <option value="none">None — passages only</option>
              <option value="gemini">Google Gemini (free tier)</option>
              <option value="anthropic">Anthropic Claude</option>
              <option value="azure_openai">Azure OpenAI</option>
              <option value="bedrock">AWS Bedrock</option>
            </select>
          </label>
          {MODEL_FIELD[llm] && (
            <label className="field">
              <span>{llm === "azure_openai" ? "Chat deployment" : "Model"}</span>
              <input
                value={values[MODEL_FIELD[llm]] ?? ""}
                onChange={(e) => set(MODEL_FIELD[llm], e.target.value)}
                spellCheck={false}
                disabled={locked}
              />
            </label>
          )}
        </div>

        {keyName && (
          <div className="key-box">
            <label className="field">
              <span>
                <Icon name="key" size={14} /> {keyInfo?.label} API key
              </span>
              <input
                type="password"
                value={keyValue}
                onChange={(e) => setKeyValue(e.target.value)}
                placeholder={keyInfo?.present ? "Paste a new key to replace the current one" : "Paste your key"}
                autoComplete="off"
                spellCheck={false}
                disabled={locked}
              />
            </label>
            <div className="row gap wrap">
              <label className="check">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} disabled={locked} />
                Remember on this computer
              </label>
              <span className={`key-state ${keyInfo?.present ? "ok" : "missing"}`}>
                <span className={`pip ${keyInfo?.present ? "ok" : "warn"}`} />
                {SOURCE_LABEL[keyInfo?.source] ?? "Not set"}
                {keyInfo?.hint && keyInfo.present ? ` · ${keyInfo.hint}` : ""}
              </span>
              {keyInfo?.saved_on_disk && !locked && (
                <button className="link-btn" onClick={forgetKey}>
                  Forget saved key
                </button>
              )}
            </div>
            <p className="muted small">
              {KEY_HELP[llm]?.text}{" "}
              {KEY_HELP[llm]?.url && (
                <a href={KEY_HELP[llm].url} target="_blank" rel="noopener noreferrer">
                  Open
                </a>
              )}
              <br />
              Saved keys go in <code>{status.keystore_path}</code>, readable only by your user — never in the project
              folder or anything git uploads. Never paste keys into chats or documents.
            </p>
          </div>
        )}
        {llm === "bedrock" && (
          <p className="muted small">Bedrock uses your normal AWS credentials (<code>aws configure</code>).</p>
        )}

        <div className="row gap wrap">
          <button className="btn primary" onClick={saveAi} disabled={locked || saving || busy}>
            Save
          </button>
          {llm !== "none" && (
            <button className="btn" onClick={runTest} disabled={test?.running || settings.values["llm.provider"] !== llm}>
              {test?.running ? <span className="spinner" /> : <Icon name="check" />} Test connection
            </button>
          )}
          {test && !test.running && (
            <span className={`test-result ${test.ok ? "ok" : "bad"}`} role="status">
              {test.ok ? `Connected — replied “${test.reply}” in ${test.seconds}s` : test.error}
            </span>
          )}
          {settings.values["llm.provider"] !== llm && <span className="muted small">Save first, then test.</span>}
        </div>
        {status.llm.error && settings.values["llm.provider"] === llm && (
          <div className="note warn">
            <Icon name="alert" size={16} /> {status.llm.error}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Where the knowledge base lives</h2>
        <p className="muted">
          Each backend keeps its own index — switching never deletes anything. After switching to a new backend, add your
          documents again.
        </p>
        <div className="backends">
          {status.backends.map((b) => (
            <label key={b.name} className={`backend${provider === b.name ? " on" : ""}`}>
              <input
                type="radio"
                name="backend"
                value={b.name}
                checked={provider === b.name}
                onChange={() => setProvider(b.name)}
                disabled={locked}
              />
              <Icon name={b.name === "local" ? "laptop" : "cloud"} />
              <div>
                <strong>{b.label}</strong>
                <div className="muted small">
                  {b.name === "local"
                    ? "Free. Nothing leaves this computer except questions sent to your AI model."
                    : !b.installed
                      ? `Needs extra packages: pipx inject knowledge-keeper "knowledge-keeper[${b.name}]"`
                      : !b.configured
                        ? "Installed — add your endpoints below."
                        : "Ready."}
                </div>
              </div>
              <span className="muted small">{b.documents} docs</span>
            </label>
          ))}
        </div>
        {provider !== "local" && (
          <div className="grid2">
            {(provider === "azure" ? AZURE_FIELDS : AWS_FIELDS).map(([k, label, ph]) => (
              <label className="field" key={k}>
                <span>{label}</span>
                <input value={values[k] ?? ""} placeholder={ph} onChange={(e) => set(k, e.target.value)} spellCheck={false} disabled={locked} />
              </label>
            ))}
          </div>
        )}
        {provider !== "local" && (
          <p className="muted small">
            Cloud backends cost money while they exist — see the README's cost notes before provisioning.
            {provider === "aws" && " AWS credentials come from your normal AWS setup (aws configure)."}
          </p>
        )}
        {status.backend_error && provider === status.provider && (
          <div className="note warn">
            <Icon name="alert" size={16} /> {status.backend_error}
          </div>
        )}
        <div className="row gap">
          <button className="btn primary" onClick={saveBackend} disabled={locked || saving || busy}>
            Save storage settings
          </button>
        </div>
      </div>

      <div className="card">
        <h2>This computer</h2>
        <dl className="facts">
          <dt>Workspace</dt>
          <dd>
            <code>{status.workspace}</code>
          </dd>
          <dt>Documents folder</dt>
          <dd>
            <code>{status.documents_dir}</code>
          </dd>
          <dt>Version</dt>
          <dd>{status.version}</dd>
        </dl>
        {isLocal && (
          <div className="row gap wrap">
            <button className="btn" onClick={() => post("/api/open-folder", { which: "workspace" }).catch((e) => toast(e.message, "error"))}>
              <Icon name="external" /> Open workspace folder
            </button>
            <button
              className="btn"
              onClick={() =>
                post("/api/shortcut")
                  .then((r) => toast(r.message, "success"))
                  .catch((e) => toast(e.message, "error"))
              }
            >
              <Icon name="pin" /> {SHORTCUT_LABEL[status.platform] ?? "Add to app menu"}
            </button>
          </div>
        )}
        <ShareInfo share={status.share} />
      </div>
    </section>
  );
}

function ShareInfo({ share }) {
  if (share?.enabled) {
    return (
      <div className="share on">
        <Icon name="share" />
        <div>
          <strong>Shared on your network</strong>
          <p className="muted small">
            Other devices on this network can open <code>{share.url}</code> and enter access code <code>{share.code}</code>.
            They can ask questions and add documents, but not change settings.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="share">
      <Icon name="share" />
      <div>
        <strong>Use it from another device</strong>
        <p className="muted small">
          Start Knowledge Keeper with <code>kk-gui --share</code> to open it from your phone or another computer on the same
          network. Visitors need an access code shown here.
        </p>
      </div>
    </div>
  );
}
