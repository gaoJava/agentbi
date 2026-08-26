import React from 'react';
import ReactDOM from 'react-dom';
import InsightPilotPanel from './InsightPilotPanel';
import { installSupersetContextBridge } from './supersetContextBridge';

const ROOT_ID = 'agentbi-insight-pilot-root';

function mountInsightPilot(): void {
  if (document.getElementById(ROOT_ID)) return;

  const root = document.createElement('div');
  root.id = ROOT_ID;
  document.body.appendChild(root);
  installSupersetContextBridge();
  ReactDOM.render(<InsightPilotPanel />, root);
}

mountInsightPilot();
