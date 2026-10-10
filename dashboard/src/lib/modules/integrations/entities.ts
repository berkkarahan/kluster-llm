export type ChatMessage = {
  role: 'user' | 'assistant' | 'system';
  content: string;
};

export type ConnectionSnapshot = {
  role: 'master';
  node_id: string;
  url: string;
  model: string;
  api_token: string;
};
