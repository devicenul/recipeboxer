export interface SourceCapabilities {
  supports_containers: boolean;
  supports_search: boolean;
  supports_thumbnails: boolean;
  supports_pagination: boolean;
  supports_streaming: boolean;
  requires_auth: boolean;
  supported_mime_types?: string[];
  category_filters?: string[];
}

export interface SourceItem {
  id: string;
  name: string;
  mime_type: string;
  size_bytes?: number | null;
  width?: number | null;
  height?: number | null;
  thumbnail_url?: string | null;
  download_url?: string | null;
  sha256_hash?: string | null;
  container_id?: string | null;
  created_at?: string | null;
  metadata?: Record<string, any>;
}

export interface SourceContainer {
  id: string;
  name: string;
  item_count?: number | null;
  cover_image_url?: string | null;
  container_type: string;
  metadata?: Record<string, any>;
}

export interface SourceProviderConfig {
  provider_id: string;
  auth_token?: string | null;
  client_id?: string | null;
  client_secret?: string | null;
  refresh_token?: string | null;
  token_uri?: string | null;
  bucket_name?: string | null;
  prefix?: string | null;
  endpoint_url?: string | null;
  region_name?: string | null;
  access_key_id?: string | null;
  secret_access_key?: string | null;
  credentials_json?: string | null;
  local_path?: string | null;
  category_filter?: string | null;
  extra?: Record<string, any>;
}

export interface SourceConnectionStatus {
  provider_id: string;
  name: string;
  description: string;
  connected: boolean;
  auth_required: boolean;
  authenticated: boolean;
  message: string;
  capabilities: SourceCapabilities;
}

export interface ListContainersResponse {
  containers: SourceContainer[];
  next_page_token?: string | null;
  total_count?: number | null;
}

export interface ListItemsResponse {
  items: SourceItem[];
  next_page_token?: string | null;
  total_count?: number | null;
}
