import { useEffect, useMemo } from 'react';
import type { KeyboardEvent } from 'react';
import type { KnowledgeGraphEdge, KnowledgeGraphNode } from '../api/smartEngine';

export type GraphLayoutMode = 'path' | 'network' | 'radial';
export type NeighborhoodDepth = 0 | 1 | 2;

export interface GraphFilterState {
  statusFilter: Set<KnowledgeGraphNode['status']>;
  edgeTypeFilter: Set<KnowledgeGraphEdge['type']>;
  layoutMode: GraphLayoutMode;
  neighborhoodDepth: NeighborhoodDepth;
  searchQuery: string;
  highlightRecommendedPath: boolean;
}

export interface FilteredGraphElements {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  searchMatches: KnowledgeGraphNode[];
  visibleNodeKeys: Set<string>;
  matchedNodeKeys: Set<string>;
  recommendedKeys: Set<string>;
  neighborhoodRootKey: string;
  sparseNeighborhoodFallback: boolean;
}

export type EdgeTypeCounts = Record<KnowledgeGraphEdge['type'], number>;

interface KnowledgeGraphCanvasProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  selectedNodeKey: string;
  nextRecommended?: string[];
  statusFilter: Set<KnowledgeGraphNode['status']>;
  edgeTypeFilter: Set<KnowledgeGraphEdge['type']>;
  layoutMode: GraphLayoutMode;
  neighborhoodDepth: NeighborhoodDepth;
  searchQuery: string;
  highlightRecommendedPath: boolean;
  onSelectNode: (nodeKey: string) => void;
  onResetView?: () => void;
}

interface GraphPoint {
  key: string;
  x: number;
  y: number;
  radius: number;
  node: KnowledgeGraphNode;
}

const GRAPH_WIDTH = 980;
const GRAPH_HEIGHT = 720;
const GRAPH_CENTER_X = GRAPH_WIDTH / 2;
const GRAPH_CENTER_Y = GRAPH_HEIGHT / 2;
const MIN_NODE_GAP = 102;
const SPARSE_GRAPH_EDGE_LIMIT = 3;
const MIN_OVERVIEW_NODE_COUNT = 8;
const MAX_OVERVIEW_NODE_COUNT = 14;

export const ALL_NODE_STATUSES: KnowledgeGraphNode['status'][] = [
  'WEAK',
  'IN_PROGRESS',
  'MASTERED',
  'NOT_STARTED',
];

export const ALL_EDGE_TYPES: KnowledgeGraphEdge['type'][] = [
  'PREREQUISITE',
  'RELATED',
  'PART_OF',
];

export const STATUS_LABELS: Record<KnowledgeGraphNode['status'], string> = {
  MASTERED: '已掌握',
  WEAK: '薄弱',
  IN_PROGRESS: '学习中',
  NOT_STARTED: '未开始',
};

export const EDGE_TYPE_LABELS: Record<KnowledgeGraphEdge['type'], string> = {
  PREREQUISITE: '前置',
  RELATED: '相关',
  PART_OF: '属于',
};

const STATUS_COLORS: Record<KnowledgeGraphNode['status'], string> = {
  MASTERED: '#10b981',
  WEAK: '#f59e0b',
  IN_PROGRESS: '#3b82f6',
  NOT_STARTED: '#94a3b8',
};

const EDGE_COLORS: Record<KnowledgeGraphEdge['type'], string> = {
  PREREQUISITE: '#2563eb',
  RELATED: '#a855f7',
  PART_OF: '#0f766e',
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function normalizeMastery(value: number): number {
  return Number.isFinite(value) ? clamp(value, 0, 1) : 0;
}

function graphNodeRadius(mastery: number): number {
  return 36 + normalizeMastery(mastery) * 16;
}

function truncateLabel(label: string, maxLength = 13): string {
  const chars = [...label];
  return chars.length > maxLength ? `${chars.slice(0, maxLength - 1).join('')}...` : label;
}

function splitNodeLabel(label: string): string[] {
  const normalized = label
    .trim()
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ');
  if (!normalized) {
    return [];
  }
  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length > 1) {
    const lines: string[] = [];
    let currentLine = '';
    for (const word of words) {
      const candidate = currentLine ? `${currentLine} ${word}` : word;
      if ([...candidate].length <= 12) {
        currentLine = candidate;
        continue;
      }
      if (currentLine) {
        lines.push(currentLine);
      }
      currentLine = word;
      if (lines.length === 1) {
        break;
      }
    }
    if (currentLine) {
      lines.push(currentLine);
    }
    return lines.slice(0, 2).map((line, index) => (
      index === 1 && lines.length > 2 ? truncateLabel(line, 12) : line
    ));
  }

  const chars = [...normalized];
  if (chars.length <= 9) {
    return [normalized];
  }
  const firstLine = chars.slice(0, 9).join('');
  const secondLine = chars.length > 18
    ? `${chars.slice(9, 16).join('')}...`
    : chars.slice(9).join('');
  return [firstLine, secondLine].filter(Boolean);
}

function normalizeSearchText(value: string): string {
  return value.trim().toLowerCase();
}

export function getSearchMatches(nodes: KnowledgeGraphNode[], query: string): KnowledgeGraphNode[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) {
    return [];
  }
  return nodes.filter((node) => {
    const haystack = `${node.topic} ${node.source} ${node.key}`.toLowerCase();
    return haystack.includes(normalizedQuery);
  });
}

export function getNeighborhoodNodeKeys(
  edges: KnowledgeGraphEdge[],
  rootKey: string,
  depth: NeighborhoodDepth,
): Set<string> {
  const result = new Set<string>();
  if (!rootKey || depth === 0) {
    return result;
  }

  result.add(rootKey);
  let frontier = new Set([rootKey]);
  for (let step = 0; step < depth; step += 1) {
    const nextFrontier = new Set<string>();
    for (const edge of edges) {
      if (frontier.has(edge.from) && !result.has(edge.to)) {
        nextFrontier.add(edge.to);
      }
      if (frontier.has(edge.to) && !result.has(edge.from)) {
        nextFrontier.add(edge.from);
      }
    }
    if (nextFrontier.size === 0) {
      break;
    }
    for (const key of nextFrontier) {
      result.add(key);
    }
    frontier = nextFrontier;
  }
  return result;
}

export function chooseGraphLayout(layoutMode: GraphLayoutMode, hasEdges: boolean): GraphLayoutMode {
  if (!hasEdges && layoutMode === 'path') {
    return 'radial';
  }
  return layoutMode;
}

export function countEdgesByType(edges: KnowledgeGraphEdge[]): EdgeTypeCounts {
  return edges.reduce<EdgeTypeCounts>(
    (counts, edge) => ({
      ...counts,
      [edge.type]: counts[edge.type] + 1,
    }),
    {
      PREREQUISITE: 0,
      RELATED: 0,
      PART_OF: 0,
    },
  );
}

export function filterGraphElements(
  nodes: KnowledgeGraphNode[],
  edges: KnowledgeGraphEdge[],
  filters: GraphFilterState,
  selectedNodeKey: string,
  nextRecommended: string[] = [],
): FilteredGraphElements {
  const allNodeKeys = new Set(nodes.map((node) => node.key));
  const statusFilteredNodes = nodes.filter((node) => filters.statusFilter.has(node.status));
  const statusFilteredKeys = new Set(statusFilteredNodes.map((node) => node.key));
  const typeFilteredEdges = edges.filter((edge) => (
    allNodeKeys.has(edge.from)
    && allNodeKeys.has(edge.to)
    && filters.edgeTypeFilter.has(edge.type)
  ));
  const searchMatches = getSearchMatches(statusFilteredNodes, filters.searchQuery);
  const neighborhoodRootKey = chooseNeighborhoodRoot(
    statusFilteredKeys,
    selectedNodeKey,
    nextRecommended,
    statusFilteredNodes,
  );

  let visibleNodeKeys = new Set(statusFilteredKeys);
  let sparseNeighborhoodFallback = false;
  if (filters.neighborhoodDepth > 0) {
    const neighborhoodKeys = getNeighborhoodNodeKeys(typeFilteredEdges, neighborhoodRootKey, filters.neighborhoodDepth);
    visibleNodeKeys = new Set([...statusFilteredKeys].filter((key) => neighborhoodKeys.has(key)));
    if (shouldUseSparseNeighborhoodFallback(typeFilteredEdges, visibleNodeKeys, statusFilteredKeys)) {
      visibleNodeKeys = expandSparseNeighborhoodOverview(
        visibleNodeKeys,
        statusFilteredNodes,
        nextRecommended,
        neighborhoodRootKey,
      );
      sparseNeighborhoodFallback = true;
    }
  }

  const visibleNodes = statusFilteredNodes.filter((node) => visibleNodeKeys.has(node.key));
  const stableVisibleNodeKeys = new Set(visibleNodes.map((node) => node.key));
  const visibleEdges = typeFilteredEdges.filter((edge) => (
    stableVisibleNodeKeys.has(edge.from) && stableVisibleNodeKeys.has(edge.to)
  ));
  const matchedNodeKeys = new Set(searchMatches.map((node) => node.key));

  return {
    nodes: visibleNodes,
    edges: visibleEdges,
    searchMatches,
    visibleNodeKeys: stableVisibleNodeKeys,
    matchedNodeKeys,
    recommendedKeys: new Set(nextRecommended),
    neighborhoodRootKey,
    sparseNeighborhoodFallback,
  };
}

function shouldUseSparseNeighborhoodFallback(
  edges: KnowledgeGraphEdge[],
  visibleNodeKeys: Set<string>,
  statusFilteredKeys: Set<string>,
): boolean {
  if (edges.length > SPARSE_GRAPH_EDGE_LIMIT) {
    return false;
  }
  if (statusFilteredKeys.size <= MIN_OVERVIEW_NODE_COUNT) {
    return false;
  }
  return visibleNodeKeys.size < Math.min(4, statusFilteredKeys.size);
}

function expandSparseNeighborhoodOverview(
  currentKeys: Set<string>,
  nodes: KnowledgeGraphNode[],
  nextRecommended: string[],
  rootKey: string,
): Set<string> {
  const expanded = new Set(currentKeys);
  if (rootKey) {
    expanded.add(rootKey);
  }
  for (const key of nextRecommended) {
    expanded.add(key);
  }
  for (const node of sortNodesForDisplay(nodes, nextRecommended)) {
    expanded.add(node.key);
    if (expanded.size >= MAX_OVERVIEW_NODE_COUNT) {
      break;
    }
  }
  return new Set([...expanded].filter((key) => nodes.some((node) => node.key === key)));
}

function chooseNeighborhoodRoot(
  visibleNodeKeys: Set<string>,
  selectedNodeKey: string,
  nextRecommended: string[],
  visibleNodes: KnowledgeGraphNode[],
): string {
  if (selectedNodeKey && visibleNodeKeys.has(selectedNodeKey)) {
    return selectedNodeKey;
  }
  return nextRecommended.find((key) => visibleNodeKeys.has(key)) ?? visibleNodes[0]?.key ?? '';
}

function sortNodesForDisplay(nodes: KnowledgeGraphNode[], nextRecommended: string[]): KnowledgeGraphNode[] {
  const recommended = new Set(nextRecommended);
  const statusRank: Record<KnowledgeGraphNode['status'], number> = {
    WEAK: 0,
    IN_PROGRESS: 1,
    NOT_STARTED: 2,
    MASTERED: 3,
  };
  return [...nodes].sort((left, right) => {
    const recommendedDelta = Number(recommended.has(right.key)) - Number(recommended.has(left.key));
    if (recommendedDelta !== 0) {
      return recommendedDelta;
    }
    const statusDelta = statusRank[left.status] - statusRank[right.status];
    if (statusDelta !== 0) {
      return statusDelta;
    }
    return normalizeMastery(left.mastery) - normalizeMastery(right.mastery);
  });
}

function buildGraphLayout(
  nodes: KnowledgeGraphNode[],
  edges: KnowledgeGraphEdge[],
  layoutMode: GraphLayoutMode,
  selectedNodeKey: string,
  nextRecommended: string[],
  sparseNeighborhoodFallback = false,
): Map<string, GraphPoint> {
  if (sparseNeighborhoodFallback && nodes.length > 2) {
    return buildRadialLayout(nodes, edges, selectedNodeKey, nextRecommended);
  }
  const effectiveLayout = chooseGraphLayout(layoutMode, edges.length > 0);
  if (effectiveLayout === 'network') {
    return buildNetworkLayout(nodes, nextRecommended);
  }
  if (effectiveLayout === 'radial') {
    return buildRadialLayout(nodes, edges, selectedNodeKey, nextRecommended);
  }
  return buildPathLayout(nodes, edges, nextRecommended);
}

function buildPathLayout(
  nodes: KnowledgeGraphNode[],
  edges: KnowledgeGraphEdge[],
  nextRecommended: string[],
): Map<string, GraphPoint> {
  if (nodes.length <= 1) {
    return buildSingleNodeLayout(nodes);
  }

  const pathEdges = edges.filter((edge) => edge.type === 'PREREQUISITE');
  const layoutEdges = pathEdges.length > 0 ? pathEdges : edges;
  if (layoutEdges.length < Math.max(2, nodes.length * 0.1)) {
    return buildColumnLayout(sortNodesForDisplay(nodes, nextRecommended), Math.min(4, Math.max(2, Math.ceil(nodes.length / 6))));
  }

  const nodeKeys = new Set(nodes.map((node) => node.key));
  const incomingCount = new Map<string, number>();
  const outgoingEdges = new Map<string, KnowledgeGraphEdge[]>();
  const levelByKey = new Map<string, number>();
  for (const node of nodes) {
    incomingCount.set(node.key, 0);
    outgoingEdges.set(node.key, []);
    levelByKey.set(node.key, 0);
  }
  for (const edge of layoutEdges) {
    if (!nodeKeys.has(edge.from) || !nodeKeys.has(edge.to)) {
      continue;
    }
    incomingCount.set(edge.to, (incomingCount.get(edge.to) ?? 0) + 1);
    outgoingEdges.get(edge.from)?.push(edge);
  }

  const queue = nodes.filter((node) => (incomingCount.get(node.key) ?? 0) === 0).map((node) => node.key);
  const visited = new Set<string>();
  while (queue.length > 0) {
    const key = queue.shift() ?? '';
    if (!key || visited.has(key)) {
      continue;
    }
    visited.add(key);
    for (const edge of outgoingEdges.get(key) ?? []) {
      levelByKey.set(edge.to, Math.max(levelByKey.get(edge.to) ?? 0, (levelByKey.get(key) ?? 0) + 1));
      incomingCount.set(edge.to, Math.max(0, (incomingCount.get(edge.to) ?? 0) - 1));
      if ((incomingCount.get(edge.to) ?? 0) === 0) {
        queue.push(edge.to);
      }
    }
  }

  if (visited.size < Math.max(2, Math.floor(nodes.length * 0.25))) {
    return buildColumnLayout(sortNodesForDisplay(nodes, nextRecommended), Math.min(4, Math.max(2, Math.ceil(nodes.length / 6))));
  }

  const grouped = new Map<number, KnowledgeGraphNode[]>();
  for (const node of sortNodesForDisplay(nodes, nextRecommended)) {
    const level = visited.has(node.key) ? levelByKey.get(node.key) ?? 0 : Math.min(4, (grouped.size + 1) % 5);
    grouped.set(level, [...(grouped.get(level) ?? []), node]);
  }
  return placeLevelGroups(grouped);
}

function buildColumnLayout(nodes: KnowledgeGraphNode[], columnCount: number): Map<string, GraphPoint> {
  const pointByKey = new Map<string, GraphPoint>();
  if (nodes.length === 0) {
    return pointByKey;
  }
  const safeColumnCount = Math.max(1, columnCount);
  const rowsPerColumn = Math.ceil(nodes.length / safeColumnCount);
  const startX = 136;
  const endX = GRAPH_WIDTH - 136;
  const usableWidth = endX - startX;
  const topY = 104;
  const bottomY = GRAPH_HEIGHT - 142;
  const usableHeight = bottomY - topY;

  nodes.forEach((node, index) => {
    const column = Math.floor(index / rowsPerColumn);
    const row = index % rowsPerColumn;
    const rowsInColumn = Math.min(rowsPerColumn, nodes.length - column * rowsPerColumn);
    const x = safeColumnCount === 1 ? GRAPH_CENTER_X : startX + (column / (safeColumnCount - 1)) * usableWidth;
    const y = rowsInColumn === 1 ? GRAPH_CENTER_Y : topY + (row / (rowsInColumn - 1)) * usableHeight;
    pointByKey.set(node.key, createPoint(node, x, y));
  });
  return pointByKey;
}

function placeLevelGroups(grouped: Map<number, KnowledgeGraphNode[]>): Map<string, GraphPoint> {
  const pointByKey = new Map<string, GraphPoint>();
  const levels = [...grouped.keys()].sort((left, right) => left - right);
  const startX = 126;
  const endX = GRAPH_WIDTH - 126;
  const topY = 104;
  const bottomY = GRAPH_HEIGHT - 142;
  const usableWidth = endX - startX;
  const usableHeight = bottomY - topY;

  levels.forEach((level, levelIndex) => {
    const group = grouped.get(level) ?? [];
    const x = levels.length === 1 ? GRAPH_CENTER_X : startX + (levelIndex / (levels.length - 1)) * usableWidth;
    const gap = Math.max(MIN_NODE_GAP, Math.min(112, usableHeight / Math.max(1, group.length - 1)));
    const startY = GRAPH_CENTER_Y - ((group.length - 1) * gap) / 2;
    group.forEach((node, rowIndex) => {
      const y = clamp(startY + rowIndex * gap, topY, bottomY);
      pointByKey.set(node.key, createPoint(node, x, y));
    });
  });
  return pointByKey;
}

function buildNetworkLayout(nodes: KnowledgeGraphNode[], nextRecommended: string[]): Map<string, GraphPoint> {
  if (nodes.length <= 1) {
    return buildSingleNodeLayout(nodes);
  }

  const recommended = new Set(nextRecommended);
  const pointByKey = new Map<string, GraphPoint>();
  const recommendedNodes = sortNodesForDisplay(nodes.filter((node) => recommended.has(node.key)), nextRecommended);
  const otherNodes = nodes.filter((node) => !recommended.has(node.key));
  const clusters: Array<{ nodes: KnowledgeGraphNode[]; x: number; y: number; width: number; height: number }> = [
    { nodes: otherNodes.filter((node) => node.status === 'WEAK'), x: GRAPH_WIDTH * 0.24, y: 218, width: 330, height: 260 },
    { nodes: otherNodes.filter((node) => node.status === 'IN_PROGRESS'), x: GRAPH_WIDTH * 0.76, y: 218, width: 330, height: 260 },
    { nodes: otherNodes.filter((node) => node.status === 'NOT_STARTED'), x: GRAPH_WIDTH * 0.24, y: 528, width: 330, height: 260 },
    { nodes: otherNodes.filter((node) => node.status === 'MASTERED'), x: GRAPH_WIDTH * 0.76, y: 528, width: 330, height: 260 },
    { nodes: recommendedNodes, x: GRAPH_CENTER_X, y: GRAPH_CENTER_Y, width: 330, height: 220 },
  ];

  for (const cluster of clusters) {
    placeClusterNodes(cluster.nodes, cluster.x, cluster.y, cluster.width, cluster.height, pointByKey);
  }
  return pointByKey;
}

function placeClusterNodes(
  nodes: KnowledgeGraphNode[],
  centerX: number,
  centerY: number,
  width: number,
  height: number,
  pointByKey: Map<string, GraphPoint>,
): void {
  if (nodes.length === 0) {
    return;
  }
  if (nodes.length === 1) {
    pointByKey.set(nodes[0].key, createPoint(nodes[0], centerX, centerY));
    return;
  }
  const columns = Math.ceil(Math.sqrt(nodes.length));
  const rows = Math.ceil(nodes.length / columns);
  const cellWidth = Math.min(124, width / Math.max(1, columns));
  const cellHeight = Math.min(108, height / Math.max(1, rows));
  nodes.forEach((node, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = centerX + (column - (columns - 1) / 2) * cellWidth;
    const y = centerY + (row - (rows - 1) / 2) * cellHeight;
    pointByKey.set(node.key, createPoint(node, clamp(x, 76, GRAPH_WIDTH - 76), clamp(y, 76, GRAPH_HEIGHT - 116)));
  });
}

function buildRadialLayout(
  nodes: KnowledgeGraphNode[],
  edges: KnowledgeGraphEdge[],
  selectedNodeKey: string,
  nextRecommended: string[],
): Map<string, GraphPoint> {
  if (nodes.length <= 1) {
    return buildSingleNodeLayout(nodes);
  }
  const nodeKeys = new Set(nodes.map((node) => node.key));
  const rootKey = selectedNodeKey && nodeKeys.has(selectedNodeKey)
    ? selectedNodeKey
    : nextRecommended.find((key) => nodeKeys.has(key)) ?? nodes[0].key;
  const distanceByKey = buildDistanceMap(edges, rootKey);
  const grouped = new Map<number, KnowledgeGraphNode[]>();
  for (const node of sortNodesForDisplay(nodes, nextRecommended)) {
    const distance = distanceByKey.get(node.key);
    const ring = distance === undefined ? 3 : Math.min(distance, 3);
    grouped.set(ring, [...(grouped.get(ring) ?? []), node]);
  }

  const pointByKey = new Map<string, GraphPoint>();
  const ringRadius: Record<number, { x: number; y: number }> = {
    0: { x: 0, y: 0 },
    1: { x: 210, y: 155 },
    2: { x: 310, y: 225 },
    3: { x: 390, y: 285 },
  };
  for (const [ring, group] of grouped.entries()) {
    if (ring === 0) {
      group.forEach((node, index) => {
        const x = group.length === 1 ? GRAPH_CENTER_X : GRAPH_CENTER_X + (index - (group.length - 1) / 2) * MIN_NODE_GAP;
        pointByKey.set(node.key, createPoint(node, x, GRAPH_CENTER_Y));
      });
      continue;
    }
    group.forEach((node, index) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / group.length;
      const radius = ringRadius[ring] ?? { x: 310, y: 225 };
      pointByKey.set(
        node.key,
        createPoint(
          node,
          GRAPH_CENTER_X + Math.cos(angle) * radius.x,
          GRAPH_CENTER_Y + Math.sin(angle) * radius.y,
        ),
      );
    });
  }
  return pointByKey;
}

function buildDistanceMap(edges: KnowledgeGraphEdge[], rootKey: string): Map<string, number> {
  const distanceByKey = new Map<string, number>();
  if (!rootKey) {
    return distanceByKey;
  }
  distanceByKey.set(rootKey, 0);
  const queue = [rootKey];
  while (queue.length > 0) {
    const key = queue.shift() ?? '';
    const currentDistance = distanceByKey.get(key) ?? 0;
    for (const edge of edges) {
      const nextKey = edge.from === key ? edge.to : edge.to === key ? edge.from : '';
      if (!nextKey || distanceByKey.has(nextKey)) {
        continue;
      }
      distanceByKey.set(nextKey, currentDistance + 1);
      queue.push(nextKey);
    }
  }
  return distanceByKey;
}

function buildSingleNodeLayout(nodes: KnowledgeGraphNode[]): Map<string, GraphPoint> {
  const pointByKey = new Map<string, GraphPoint>();
  const node = nodes[0];
  if (node) {
    pointByKey.set(node.key, createPoint(node, GRAPH_CENTER_X, GRAPH_CENTER_Y));
  }
  return pointByKey;
}

function createPoint(node: KnowledgeGraphNode, x: number, y: number): GraphPoint {
  return {
    key: node.key,
    x,
    y,
    radius: graphNodeRadius(node.mastery),
    node,
  };
}

function buildNeighborKeys(edges: KnowledgeGraphEdge[], selectedNodeKey: string): Set<string> {
  const neighbors = new Set<string>();
  if (!selectedNodeKey) {
    return neighbors;
  }
  for (const edge of edges) {
    if (edge.from === selectedNodeKey) {
      neighbors.add(edge.to);
    }
    if (edge.to === selectedNodeKey) {
      neighbors.add(edge.from);
    }
  }
  return neighbors;
}

function edgePath(edge: KnowledgeGraphEdge, pointByKey: Map<string, GraphPoint>): string {
  const source = pointByKey.get(edge.from);
  const target = pointByKey.get(edge.to);
  if (!source || !target) {
    return '';
  }
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const sourceX = source.x + (dx / distance) * (source.radius + 6);
  const sourceY = source.y + (dy / distance) * (source.radius + 6);
  const targetX = target.x - (dx / distance) * (target.radius + 12);
  const targetY = target.y - (dy / distance) * (target.radius + 12);
  const curve = edge.type === 'RELATED' ? 34 : edge.type === 'PART_OF' ? -26 : 0;
  if (curve === 0) {
    return `M ${sourceX.toFixed(1)} ${sourceY.toFixed(1)} L ${targetX.toFixed(1)} ${targetY.toFixed(1)}`;
  }
  const midX = (sourceX + targetX) / 2 - (dy / distance) * curve;
  const midY = (sourceY + targetY) / 2 + (dx / distance) * curve;
  return `M ${sourceX.toFixed(1)} ${sourceY.toFixed(1)} Q ${midX.toFixed(1)} ${midY.toFixed(1)} ${targetX.toFixed(1)} ${targetY.toFixed(1)}`;
}

function edgeLabelPoint(edge: KnowledgeGraphEdge, pointByKey: Map<string, GraphPoint>): { x: number; y: number } | null {
  const source = pointByKey.get(edge.from);
  const target = pointByKey.get(edge.to);
  if (!source || !target) {
    return null;
  }
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const sourceX = source.x + (dx / distance) * (source.radius + 6);
  const sourceY = source.y + (dy / distance) * (source.radius + 6);
  const targetX = target.x - (dx / distance) * (target.radius + 12);
  const targetY = target.y - (dy / distance) * (target.radius + 12);
  const curve = edge.type === 'RELATED' ? 34 : edge.type === 'PART_OF' ? -26 : 0;
  if (curve === 0) {
    return {
      x: (sourceX + targetX) / 2 - (dy / distance) * 18,
      y: (sourceY + targetY) / 2 + (dx / distance) * 18,
    };
  }
  return {
    x: (sourceX + targetX) / 2 - (dy / distance) * curve,
    y: (sourceY + targetY) / 2 + (dx / distance) * curve,
  };
}

function handleNodeKeyDown(event: KeyboardEvent<SVGGElement>, nodeKey: string, onSelectNode: (nodeKey: string) => void): void {
  if (event.key !== 'Enter' && event.key !== ' ') {
    return;
  }
  event.preventDefault();
  onSelectNode(nodeKey);
}

export default function KnowledgeGraphCanvas({
  nodes,
  edges,
  selectedNodeKey,
  nextRecommended = [],
  statusFilter,
  edgeTypeFilter,
  layoutMode,
  neighborhoodDepth,
  searchQuery,
  highlightRecommendedPath,
  onSelectNode,
  onResetView,
}: KnowledgeGraphCanvasProps) {
  const filters = useMemo<GraphFilterState>(() => ({
    statusFilter,
    edgeTypeFilter,
    layoutMode,
    neighborhoodDepth,
    searchQuery,
    highlightRecommendedPath,
  }), [edgeTypeFilter, highlightRecommendedPath, layoutMode, neighborhoodDepth, searchQuery, statusFilter]);
  const filteredGraph = useMemo(
    () => filterGraphElements(nodes, edges, filters, selectedNodeKey, nextRecommended),
    [edges, filters, nextRecommended, nodes, selectedNodeKey],
  );
  const pointByKey = useMemo(
    () => buildGraphLayout(
      filteredGraph.nodes,
      filteredGraph.edges,
      layoutMode,
      selectedNodeKey || filteredGraph.neighborhoodRootKey,
      nextRecommended,
      filteredGraph.sparseNeighborhoodFallback,
    ),
    [
      filteredGraph.edges,
      filteredGraph.neighborhoodRootKey,
      filteredGraph.nodes,
      filteredGraph.sparseNeighborhoodFallback,
      layoutMode,
      nextRecommended,
      selectedNodeKey,
    ],
  );
  const neighborKeys = useMemo(
    () => buildNeighborKeys(filteredGraph.edges, selectedNodeKey),
    [filteredGraph.edges, selectedNodeKey],
  );
  const hasSelectedNode = Boolean(selectedNodeKey && pointByKey.has(selectedNodeKey));
  const trimmedSearchQuery = searchQuery.trim();
  const totalEdgeCounts = useMemo(() => countEdgesByType(edges), [edges]);
  const visibleEdgeCounts = useMemo(() => countEdgesByType(filteredGraph.edges), [filteredGraph.edges]);
  const hasOriginalEdges = edges.length > 0;
  const relationSummary = `真实关系：前置 ${totalEdgeCounts.PREREQUISITE}，相关 ${totalEdgeCounts.RELATED}，属于 ${totalEdgeCounts.PART_OF}`;

  useEffect(() => {
    if (!trimmedSearchQuery || filteredGraph.searchMatches.length === 0) {
      return;
    }
    const firstMatchKey = filteredGraph.searchMatches[0].key;
    if (firstMatchKey !== selectedNodeKey) {
      onSelectNode(firstMatchKey);
    }
  }, [filteredGraph.searchMatches, onSelectNode, selectedNodeKey, trimmedSearchQuery]);

  const resetView = () => {
    onResetView?.();
    const fallbackNode = filteredGraph.neighborhoodRootKey
      || nextRecommended.find((key) => pointByKey.has(key))
      || filteredGraph.nodes[0]?.key
      || nodes[0]?.key
      || '';
    if (fallbackNode) {
      onSelectNode(fallbackNode);
    }
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950/30">
      <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-500 dark:text-slate-400">
              {ALL_NODE_STATUSES.map((status) => (
                <span key={status} className="inline-flex items-center gap-1.5">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: STATUS_COLORS[status] }}
                  />
                  {STATUS_LABELS[status]}
                </span>
              ))}
              <span className="text-slate-400 dark:text-slate-500">
                显示 {filteredGraph.nodes.length}/{nodes.length} 个知识点
              </span>
            </div>
            <div className="text-xs font-medium text-slate-500 dark:text-slate-400">
              {relationSummary}
              {filteredGraph.sparseNeighborhoodFallback ? '；当前为推荐/薄弱概览，连线仍只显示真实关系。' : ''}
            </div>
          </div>
          <button
            type="button"
            onClick={resetView}
            className="inline-flex h-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 px-3 text-xs font-semibold text-slate-600 transition hover:bg-primary-50 hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-300 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-primary-500/10 dark:hover:text-primary-200"
          >
            重置视图
          </button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-semibold text-slate-500 dark:text-slate-400">
          {ALL_EDGE_TYPES.map((type) => (
            <span
              key={type}
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 ${
                totalEdgeCounts[type] === 0
                  ? 'bg-slate-50 text-slate-400 dark:bg-slate-900/50 dark:text-slate-500'
                  : 'bg-slate-50 text-slate-600 dark:bg-slate-900/80 dark:text-slate-300'
              }`}
            >
              <span
                className={`h-px w-7 ${type === 'RELATED' ? 'border-t border-dashed' : ''}`}
                style={{ backgroundColor: type === 'RELATED' ? 'transparent' : EDGE_COLORS[type], borderColor: EDGE_COLORS[type] }}
              />
              {EDGE_TYPE_LABELS[type]}：可见 {visibleEdgeCounts[type]} / 全部 {totalEdgeCounts[type]}
            </span>
          ))}
        </div>
      </div>
      <div className="relative h-[720px] min-h-[620px]">
        {filteredGraph.nodes.length === 0 ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-slate-500 dark:text-slate-400">
            当前筛选下没有可显示的知识点
          </div>
        ) : (
          <svg
            className="absolute inset-0 h-full w-full"
            viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`}
            role="img"
            aria-label="知识图谱网络视图"
          >
            <defs>
              {Object.entries(EDGE_COLORS).map(([type, color]) => (
                <marker
                  key={type}
                  id={`knowledge-edge-arrow-${type}`}
                  markerWidth="11"
                  markerHeight="11"
                  refX="10"
                  refY="5.5"
                  orient="auto"
                  markerUnits="strokeWidth"
                >
                  <path d="M 0 0 L 11 5.5 L 0 11 z" fill={color} />
                </marker>
              ))}
              <marker
                id="knowledge-edge-arrow-recommended"
                markerWidth="11"
                markerHeight="11"
                refX="10"
                refY="5.5"
                orient="auto"
                markerUnits="strokeWidth"
              >
                <path d="M 0 0 L 11 5.5 L 0 11 z" fill="#2563eb" />
              </marker>
            </defs>

            <rect width={GRAPH_WIDTH} height={GRAPH_HEIGHT} fill="transparent" />

            {filteredGraph.edges.map((edge, index) => {
              const path = edgePath(edge, pointByKey);
              if (!path) {
                return null;
              }
              const connected = edge.from === selectedNodeKey || edge.to === selectedNodeKey;
              const recommendedEdge = highlightRecommendedPath
                && filteredGraph.recommendedKeys.has(edge.from)
                && filteredGraph.recommendedKeys.has(edge.to);
              const strokeColor = recommendedEdge ? '#2563eb' : EDGE_COLORS[edge.type];
              return (
                <path
                  key={`${edge.from}:${edge.to}:${edge.type}:${index}`}
                  d={path}
                  fill="none"
                  stroke={strokeColor}
                  strokeWidth={recommendedEdge ? 5 : connected ? 4.2 : 2.8 + normalizeMastery(edge.weight) * 2}
                  strokeDasharray={edge.type === 'RELATED' ? '5 5' : undefined}
                  opacity={!hasSelectedNode || connected || recommendedEdge ? 0.9 : 0.34}
                  markerEnd={`url(#${recommendedEdge ? 'knowledge-edge-arrow-recommended' : `knowledge-edge-arrow-${edge.type}`})`}
                />
              );
            })}

            {filteredGraph.edges.map((edge, index) => {
              const labelPoint = edgeLabelPoint(edge, pointByKey);
              if (!labelPoint) {
                return null;
              }
              const connected = edge.from === selectedNodeKey || edge.to === selectedNodeKey;
              const recommendedEdge = highlightRecommendedPath
                && filteredGraph.recommendedKeys.has(edge.from)
                && filteredGraph.recommendedKeys.has(edge.to);
              const opacity = !hasSelectedNode || connected || recommendedEdge ? 1 : 0.36;
              return (
                <g
                  key={`${edge.from}:${edge.to}:${edge.type}:${index}:label`}
                  opacity={opacity}
                >
                  <rect
                    x={labelPoint.x - 21}
                    y={labelPoint.y - 13}
                    width="42"
                    height="24"
                    rx="12"
                    fill="#ffffff"
                    fillOpacity="0.9"
                    stroke={EDGE_COLORS[edge.type]}
                    strokeOpacity="0.28"
                  />
                  <text
                    x={labelPoint.x}
                    y={labelPoint.y + 4}
                    textAnchor="middle"
                    className="fill-slate-600 text-[13px] font-bold dark:fill-slate-200"
                  >
                    {EDGE_TYPE_LABELS[edge.type]}
                  </text>
                </g>
              );
            })}

            {Array.from(pointByKey.values()).map((point) => {
              const selected = point.key === selectedNodeKey;
              const neighbor = neighborKeys.has(point.key);
              const recommended = highlightRecommendedPath && filteredGraph.recommendedKeys.has(point.key);
              const matched = filteredGraph.matchedNodeKeys.has(point.key);
              const dimmed = !filteredGraph.sparseNeighborhoodFallback
                && hasSelectedNode
                && !selected
                && !neighbor
                && !recommended
                && !matched;
              const masteryPercent = Math.round(normalizeMastery(point.node.mastery) * 100);
              const labelLines = splitNodeLabel(point.node.topic);
              return (
                <g
                  key={point.key}
                  role="button"
                  tabIndex={0}
                  aria-label={`${point.node.topic}，${STATUS_LABELS[point.node.status]}，掌握度 ${masteryPercent}%`}
                  className={`cursor-pointer outline-none ${matched ? 'matched' : ''}`}
                  opacity={dimmed ? 0.34 : 1}
                  onClick={() => onSelectNode(point.key)}
                  onKeyDown={(event) => handleNodeKeyDown(event, point.key, onSelectNode)}
                >
                  <title>{`${point.node.topic} · ${STATUS_LABELS[point.node.status]} · ${masteryPercent}%`}</title>
                  {recommended ? (
                    <circle
                      cx={point.x}
                      cy={point.y}
                      r={point.radius + 10}
                      fill="none"
                      stroke="#2563eb"
                      strokeWidth="2.5"
                      strokeOpacity="0.42"
                    />
                  ) : null}
                  {matched ? (
                    <circle
                      cx={point.x}
                      cy={point.y}
                      r={point.radius + 14}
                      fill="none"
                      stroke="#f97316"
                      strokeWidth="2.5"
                      strokeDasharray="5 4"
                    />
                  ) : null}
                  <circle
                    cx={point.x}
                    cy={point.y}
                    r={point.radius}
                    fill={STATUS_COLORS[point.node.status]}
                    stroke={selected ? '#0f172a' : neighbor ? '#60a5fa' : '#ffffff'}
                    strokeWidth={selected ? 4 : neighbor ? 3 : 2.4}
                  />
                  <text
                    x={point.x}
                    y={point.y + point.radius + 24}
                    textAnchor="middle"
                    className="fill-slate-700 text-[16px] font-bold dark:fill-slate-200"
                    paintOrder="stroke"
                    stroke="#ffffff"
                    strokeWidth="5"
                  >
                    {labelLines.map((line, lineIndex) => (
                      <tspan
                        key={`${point.key}-label-${lineIndex}`}
                        x={point.x}
                        dy={lineIndex === 0 ? 0 : 17}
                      >
                        {truncateLabel(line, 12)}
                      </tspan>
                    ))}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        {filteredGraph.nodes.length > 0 && filteredGraph.edges.length === 0 ? (
          <div className="pointer-events-none absolute right-3 top-3 max-w-[280px] rounded-xl bg-white/92 px-3 py-2 text-xs leading-5 text-slate-500 shadow-sm shadow-slate-200/70 backdrop-blur dark:bg-slate-950/86 dark:text-slate-400 dark:shadow-none">
            {hasOriginalEdges ? '当前筛选/邻域暂无可见关系，可切到全图或放开关系筛选。' : '当前画像暂无可见关系，刷新画像或生成学习路径后会补全前置/属于/相关。'}
          </div>
        ) : null}
        <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-2 rounded-xl bg-white/88 px-3 py-2 text-xs font-medium text-slate-500 shadow-sm shadow-slate-200/70 backdrop-blur dark:bg-slate-950/82 dark:text-slate-400 dark:shadow-none">
          <span className="inline-flex items-center gap-1.5"><span className="h-px w-5 bg-primary-600" />前置</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-px w-5 bg-teal-700" />属于</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-px w-5 border-t border-dashed border-purple-500" />相关</span>
        </div>
      </div>
    </div>
  );
}
