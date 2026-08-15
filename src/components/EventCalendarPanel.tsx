import React, { useEffect, useState } from 'react';
import { Calendar, AlertCircle, Clock, CheckCircle2, AlertTriangle } from 'lucide-react';

interface EventItem {
  code: string;
  name: string;
  date: string;
  impact: 'low' | 'medium' | 'high';
  sector_focus: string;
  consensus: string | null;
  source: string | null;
  stale: boolean;
}

interface EventProximity {
  nearest_high_impact: string;
  name: string;
  days_away: number;
  consensus: string | null;
  action: 'block_premium_sell' | 'caution_downsize' | 'normal';
  note: string;
}

interface EventCalendarData {
  events: EventItem[];
  proximity: EventProximity;
}

interface EventCalendarPanelProps {
  conclusion?: any;
}

export function EventCalendarPanel({ conclusion }: EventCalendarPanelProps) {
  const [data, setData] = useState<EventCalendarData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchCalendar = async () => {
      try {
        const res = await fetch('http://127.0.0.1:8000/api/event-calendar');
        if (!res.ok) throw new Error('Failed to fetch event calendar');
        const json = await res.json();
        setData(json.panel);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };
    fetchCalendar();
  }, []);

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 shadow-xl animate-pulse flex justify-center items-center h-48">
        <div className="text-gray-400 font-medium">Loading Calendar...</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="bg-gray-900 rounded-xl border border-red-900/50 p-6">
        <div className="text-red-400 font-medium flex items-center gap-2">
          <AlertCircle className="w-5 h-5" />
          Error loading calendar: {error}
        </div>
      </div>
    );
  }

  const { events, proximity } = data;
  
  // Parse today and sort events simply to show only future ones
  const todayStr = new Date().toISOString().split('T')[0];
  const upcomingEvents = events.filter(e => e.date >= todayStr).slice(0, 5);

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 shadow-xl overflow-hidden flex flex-col">
      <div className="p-5 border-b border-gray-800 flex justify-between items-center bg-gray-800/30">
        <h2 className="text-lg font-bold text-gray-100 flex items-center gap-2">
          <Calendar className="w-5 h-5 text-blue-400" />
          Economic Calendar
        </h2>
        {proximity.action === 'block_premium_sell' ? (
          <div className="px-3 py-1 bg-red-900/30 text-red-400 border border-red-800/50 rounded-full text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> High Risk
          </div>
        ) : proximity.action === 'caution_downsize' ? (
          <div className="px-3 py-1 bg-yellow-900/30 text-yellow-400 border border-yellow-800/50 rounded-full text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5" /> Caution
          </div>
        ) : (
          <div className="px-3 py-1 bg-emerald-900/30 text-emerald-400 border border-emerald-800/50 rounded-full text-xs font-bold uppercase tracking-wider flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5" /> Clear
          </div>
        )}
      </div>

      <div className="p-5 flex-1 flex flex-col gap-4">
        {/* Proximity Banner */}
        {proximity.action !== 'normal' && (
          <div className={`p-4 rounded-lg border ${
            proximity.action === 'block_premium_sell' 
              ? 'bg-red-900/20 border-red-800/50 text-red-300'
              : 'bg-yellow-900/20 border-yellow-800/50 text-yellow-300'
          }`}>
            <p className="text-sm font-medium leading-relaxed">
              {proximity.note}
            </p>
            {proximity.consensus && (
              <p className="text-xs mt-2 opacity-80 italic">
                Consensus: {proximity.consensus}
              </p>
            )}
          </div>
        )}

        {/* Events List */}
        <div className="space-y-3 mt-1">
          {upcomingEvents.map((ev, idx) => (
            <div key={idx} className="flex gap-4 p-3 rounded-lg bg-gray-800/30 border border-gray-800 hover:border-gray-700 transition-colors group">
              <div className="flex flex-col items-center justify-center min-w-[3rem] px-2 border-r border-gray-700/50">
                <span className="text-xs font-medium text-gray-500 uppercase tracking-widest">{new Date(ev.date).toLocaleDateString('en-US', { month: 'short' })}</span>
                <span className="text-xl font-bold text-gray-200">{new Date(ev.date).getDate()}</span>
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <h4 className="font-semibold text-gray-200 text-sm group-hover:text-blue-400 transition-colors">
                    {ev.name}
                  </h4>
                  {ev.impact === 'high' && (
                    <span className="w-2 h-2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]"></span>
                  )}
                </div>
                <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {ev.stale ? 'Static' : 'Live'}
                  </span>
                  <span className="text-gray-500 truncate max-w-[150px]" title={ev.sector_focus}>
                    {ev.sector_focus}
                  </span>
                </div>
                {ev.consensus && (
                  <p className="mt-2 text-xs text-blue-300/80 italic border-l-2 border-blue-500/30 pl-2">
                    "{ev.consensus}"
                  </p>
                )}
              </div>
            </div>
          ))}
          {upcomingEvents.length === 0 && (
            <div className="text-center py-6 text-gray-500 text-sm">
              No upcoming events in window.
            </div>
          )}
        </div>
      </div>

      {/* Conclusion Footer */}
      {conclusion && (
        <div className="p-4 border-t border-gray-800 bg-gray-800/20">
          <h3 className={`text-sm font-bold uppercase tracking-wider mb-2 ${
            conclusion.posture === 'defensive' ? 'text-red-400' : 
            conclusion.posture === 'cautious' ? 'text-yellow-400' : 'text-emerald-400'
          }`}>
            {conclusion.headline}
          </h3>
          <ul className="space-y-2 mb-3">
            {conclusion.points.map((pt: string, i: number) => (
              <li key={i} className="text-xs text-gray-300 flex items-start gap-2">
                <span className="text-gray-500 mt-0.5">•</span>
                <span>{pt}</span>
              </li>
            ))}
          </ul>
          <p className="text-[10px] text-gray-500 italic border-t border-gray-800/50 pt-2">
            {conclusion.disclaimer}
          </p>
        </div>
      )}

    </div>
  );
}
