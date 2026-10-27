export interface EventDetails {
  name: string;
  location: string;
  start_date: number | string | null;
  end_date: number | string | null;
  date_label?: string;
  start_time: number;
  end_time: number;
  banner_url: string;
  site_name: string;
  url: string;
}
