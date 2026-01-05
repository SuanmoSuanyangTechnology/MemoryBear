export interface TopCardListProps {
    title:string;
    description:string;
    icon: Element;
    number: number;
    label: string;
}
export interface DashboardData {
    total_models:number;
    total_spaces:number;
    total_users:number;
    total_apps_runs: string;
}