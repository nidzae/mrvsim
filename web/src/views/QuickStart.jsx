import React, { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api.js";

// The "?" button renders docs/QUICKSTART.md (PRD F12).
export default function QuickStart({ onClose }) {
  const [md, setMd] = useState("Loading…");
  useEffect(() => { api.quickstart().then((r) => setMd(r.markdown)).catch((e) => setMd(`Could not load the guide: ${e}`)); }, []);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "flex-end" }}><button className="ghost" onClick={onClose}>close</button></div>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </div>
    </div>
  );
}
