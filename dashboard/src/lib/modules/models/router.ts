import { goto } from '$app/navigation';
import { href } from '../../shared/navigation';

export const modelsHref = href('models');

export function openRun() {
  return goto(href('run'));
}
