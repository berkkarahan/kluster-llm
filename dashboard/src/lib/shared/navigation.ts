export const routes = {
  cluster: '/',
  models: '/models',
  run: '/run',
  logs: '/logs',
  integrations: '/integrations',
  settings: '/settings',
  settingsLlama: '/settings?section=llama'
} as const;

export type RouteName = keyof typeof routes;

export function href(name: RouteName): string {
  return routes[name];
}
