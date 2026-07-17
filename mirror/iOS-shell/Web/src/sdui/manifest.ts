// TypeScript mirror of Shared/schema/manifest.schema.json.
// Keep in lockstep with App/Sources/SDUI/Manifest.swift — both are renderers of one contract.

export type JSONValue =
  | string
  | number
  | boolean
  | null
  | JSONValue[]
  | { [key: string]: JSONValue };

export interface NavItem {
  title: string;
  icon?: string;
  /** Leaf destination. Omitted for section headers that only group `children`. */
  path?: string;
  /** Optional second-level items, making this a section header in the menu. */
  children?: NavItem[];
}

export interface Manifest {
  schemaVersion: number;
  generatedAt: string;
  theme?: Theme;
  data?: Record<string, JSONValue>;
  nav?: NavItem[];
  screen: Node;
}

export interface Theme {
  colors?: Record<string, string>;
  fonts?: Record<string, JSONValue>;
  spacing?: number;
  radius?: number;
}

export interface Style {
  padding?: number;
  spacing?: number;
  color?: string;
  background?: string;
  cornerRadius?: number;
  font?: string;
  weight?: "regular" | "medium" | "semibold" | "bold";
  align?: "leading" | "center" | "trailing";
  width?: number | string;
  height?: number | string;
  opacity?: number;
  /** Allow a row of items to wrap onto multiple lines (e.g. filter chips). */
  wrap?: boolean;
  [key: string]: JSONValue | undefined;
}

export interface Action {
  type: "refresh" | "openURL" | "navigate" | "setPref" | "submit" | "focus" | "none" | string;
  url?: string;
  /** For openURL: resolve the target URL from a data path (e.g. "property.url"). */
  urlBinding?: string;
  screenId?: string;
  key?: string;
  value?: JSONValue;
  /** Resolve `value` from a data path (e.g. focus a map segment by "item.index"). */
  valueBinding?: string;
}

export interface Node {
  type: string;
  id?: string;
  props?: Record<string, JSONValue>;
  style?: Style;
  binding?: string;
  action?: Action;
  children?: Node[];
}

// The MAJOR schema version this client understands. A manifest with a higher
// schemaVersion triggers a graceful "update the shell" screen instead of a crash.
export const SUPPORTED_SCHEMA_MAJOR = 1;
