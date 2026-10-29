// The plan view's layers: how each square is coloured for each one (CSS colour tokens and an opacity over the panel
// colour), shared by the 2D plan and the 3D farm's overlays so one legend serves both.
import { mm, pct, pestName } from './util.js';

export const LAYERS = [
  { id: 'water', label: 'Water' },
  { id: 'stage', label: 'Crop stage' },
  { id: 'soil', label: 'Soil' },
  { id: 'risk', label: 'Pest risk' },
  { id: 'establish', label: 'Establishment' },
  { id: 'health', label: 'Plant health' },
];

export function cellFill(layer, s) {
  switch (layer) {
    case 'water': {
      const d = s.depth_mm;
      if (d == null) return ['var(--panel-2)', 1];
      if (d >= 0) return ['var(--water)', 0.18 + 0.72 * Math.min(1, d / 100)];
      return ['var(--mud)', 0.12 + 0.7 * Math.min(1, -d / 300)];
    }
    case 'stage':
      if (!s.planted) return ['var(--mud-soft)', 0.55];
      if (!s.alive && !s.harvested) return ['var(--mud)', 0.5];
      if (s.harvested) return ['var(--straw-soft)', 1];
      if ((s.progress ?? 0) >= 0.9) return ['var(--straw)', 0.55 + 0.4 * Math.min(1, ((s.progress ?? 0.9) - 0.9) / 0.1)];
      return ['var(--rice)', 0.2 + 0.7 * Math.min(1, (s.progress ?? 0) / 0.9)];
    case 'soil':
      return s.soil?.code === 'S' ? ['var(--rice)', 0.55] : s.soil?.code === 'A' ? ['var(--straw)', 0.65] : ['var(--mud)', 0.85];
    case 'risk': {
      if (s.risk == null) return ['var(--risk-0)', s.planted ? 1 : 0.5];
      const k = Math.min(4, Math.floor(s.risk * 5));
      return [`var(--risk-${k})`, 1];
    }
    case 'establish':
      if (s.establish == null) return ['var(--panel-2)', 1];
      return ['var(--rice)', 0.1 + 0.85 * s.establish];
    case 'health':
      if (!s.planted || s.health == null) return ['var(--panel-2)', 1];
      return ['var(--rice)', 0.1 + 0.85 * s.health];
    default:
      return ['var(--panel-2)', 1];
  }
}

export function describe(s, layer) {
  const bits = [s.sq];
  if (s.depth_mm != null) bits.push(`water ${mm(s.depth_mm)}`);
  if (s.planted) bits.push(s.alive ? (s.stage || 'planted') : s.harvested ? 'harvested' : 'dead');
  else bits.push('not planted');
  if (s.soil?.code) bits.push(`soil ${{ S: 'suitable', A: 'needs amendment', U: 'unsuitable' }[s.soil.code]}`);
  if (s.pest) bits.push(`${pestName(s.pest.pest)} seen at ${pct(s.pest.severity)}`);
  if (layer === 'risk' && s.risk != null) bits.push(`pest risk ${pct(s.risk)}`);
  if (layer === 'establish' && s.establish != null) bits.push(`establishment ${pct(s.establish)}`);
  return bits.join(', ');
}

