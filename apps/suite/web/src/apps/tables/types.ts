export interface Workspace { id: string; name: string; role: string }
export interface Field {
  id: string; name: string; description: string; type: string; config: Record<string, any>;
  required: boolean; is_unique: boolean; default: unknown; read_role: string; write_role: string; can_write: boolean;
}
export interface Query {
  table_id: string; q?: string; filters?: {field_id: string; op: string; value?: unknown}[];
  sort?: {field_id: string; direction: string}[]; fields?: string[]; deleted?: boolean;
  offset?: number; limit?: number; view_id?: string | null;
}
export interface View {id: string; name: string; rev: number; config: Query}
export interface Table {
  id: string; name: string; description: string; status: string; schema_rev: number;
  read_role: string; write_role: string; fields: Field[]; deleted_fields: Field[];
  views: View[]; can_write: boolean; can_manage: boolean;
}
export interface RecordRow {
  id: string; table_id: string; seq: number; rev: number; cells: Record<string, any>;
  relations: Record<string, {id: string; table_id: string; title: string}[]>;
  formula_errors: Record<string, string>; read_role: string; write_role: string;
  created_at: number; updated_at: number; deleted_at: number | null; can_write: boolean;
}
export interface QueryResult {records: RecordRow[]; fields: Field[]; total: number; next_offset: number | null}
export const COMPUTED = new Set(['formula', 'created_time', 'updated_time', 'created_by', 'updated_by']);
