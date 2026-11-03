"""Shared, local context selection for analysis and reply; never schedules work."""
import re


def select(context, target, cfg=None):
    cfg=cfg or {}
    stamp=target.get('_time',0)
    sources={str(key) for key in target.get('_source_ids',[target.get('_id')])}
    rows=[row for row in context if str(row.get('_id')) not in sources and not row.get('_bot_command')
        and not row.get('_record_only') and int(row.get('_time',0))<=int(stamp)]
    rows.sort(key=lambda row:row.get('_time',0))
    by_id={str(row.get('_id')):row for row in rows}
    protected=set();reason='recent_window'
    quote=str(target.get('_reply_id') or '')
    def chain(identity):
        for _ in range(4):
            row=by_id.get(identity)
            if not row:break
            protected.add(identity)
            identity=str(row.get('_reply_to_id') or row.get('_reply_id') or '')
    if quote in by_id:
        reason='explicit_reply';chain(quote)
        index=rows.index(by_id[quote])
        protected.update(str(row.get('_id')) for row in rows[max(0,index-3):index+1])
    else:
        words=re.sub(r'@\d+','',str(target.get('text',''))).strip()
        followup=bool(re.match(r'那|所以|刚才|刚刚|再|还|这(?:个|样|件)|那个|前面|继续|怎么又|为什么又',words))
        if followup or target.get('_audience')=='possibly_direct':
            horizon=max(180,min(1800,cfg.get('conversation_state_ttl_seconds',600)))
            for index in range(len(rows)-1,-1,-1):
                row=rows[index]
                if row.get('_historical') or not row.get('is_bot') or stamp-row.get('_time',0)>horizon:continue
                receiver=str(row.get('_reply_to_uid') or '')
                previous=rows[index-1] if index else {}
                grounded=(receiver==str(target.get('_uid')) if receiver else
                    bool(previous and not previous.get('is_bot') and not previous.get('_historical')
                        and str(previous.get('_uid'))==str(target.get('_uid'))
                        and 0<=row.get('_time',0)-previous.get('_time',0)<=60))
                if not grounded:continue
                reason='possible_same_person_followup';chain(str(row.get('_id')))
                if previous and str(previous.get('_uid'))==str(target.get('_uid')):
                    protected.add(str(previous.get('_id')))
                break
    required=[row for row in rows if str(row.get('_id')) in protected]
    recent=[row for row in rows if not row.get('_historical') and stamp-row.get('_time',0)<=180
        and str(row.get('_id')) not in protected][-max(1,cfg.get('context_messages',24)):]
    from image_context import compress
    # Only selected turns participate: do not refer to an image outside this view.
    selected=sorted(required+recent,key=lambda row:row.get('_time',0))
    selected=compress(selected,cfg.get('_image_context_runtime'))
    by_id={str(row.get('_id')):row for row in selected}
    required=[by_id[str(row.get('_id'))] for row in required]
    recent=[by_id[str(row.get('_id'))] for row in recent]
    return required,recent,reason
