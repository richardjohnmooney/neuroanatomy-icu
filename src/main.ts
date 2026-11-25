import './styles.css';
import { loadData } from './data';
import { state } from './state';
import { initSidebar, applyTopic } from './ui/sidebar';
import { initTutorials, openTutorialFromHash } from './ui/tutorial';
import { createSliceViewer } from './viewers/sliceViewer';
import { createModel3D } from './viewers/model3d';

async function main() {
  const data = await loadData();
  initTutorials(data);
  initSidebar(data);

  const el = (id: string) => document.getElementById(id)!;
  await Promise.all([
    createSliceViewer({
      canvas: el('canvas-ct') as HTMLCanvasElement,
      mode: 'base',
      data,
      readout: el('readout-ct'),
      sourceId: 'ct',
    }),
    createSliceViewer({
      canvas: el('canvas-stylised') as HTMLCanvasElement,
      mode: 'stylised',
      data,
      readout: el('readout-stylised'),
      sourceId: 'stylised',
    }),
    createModel3D({ container: el('container-3d'), data, readout: el('readout-3d') }),
  ]);

  applyTopic(data, state.topicId);
  openTutorialFromHash(); // after the topic, so a #tutorial= link wins
}

main().catch((e) => {
  console.error(e);
  const p = document.createElement('p');
  p.style.cssText = 'padding:1em;color:#f88';
  p.textContent = 'Failed to start: ' + String(e);
  document.body.append(p);
});
