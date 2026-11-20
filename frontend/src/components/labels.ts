export const TOOL_LABELS: Record<string, string> = {
  resolve_airports: 'Resolving airports',
  rank_expansion_candidates: 'Scoring expansion candidates',
  explain_airport_score: 'Explaining score breakdown',
  get_airport_profile: 'Loading airport profile',
  compare_congestion: 'Comparing congestion',
  analyze_route_mix: 'Analyzing route mix (BTS T-100)',
  estimate_unmet_demand: 'Running spill model',
  get_live_status: 'Querying live FAA status & weather',
  explain_methodology: 'Retrieving methodology',
}

export const toolLabel = (tool: string) => TOOL_LABELS[tool] ?? tool

export const EXAMPLE_QUESTIONS = [
  'Which airports in New England are strong candidates for terminal expansion?',
  'Compare LA and Santa Ana airport congestion levels.',
  'What is the percentage of long haul flights out of Anchorage airport?',
  'What is the unmet flight demand in SFO airport and why?',
]

export const FOLLOW_UPS = [
  'What if we weight demand growth twice as much?',
  'Why does Bangor score so high?',
  'How is the expansion score calculated?',
]
