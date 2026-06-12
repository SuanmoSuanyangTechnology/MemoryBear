import { useState, useEffect, type FC } from 'react';
import { Form,
  // TimePicker, Button,
  Input
} from 'antd';
// import { CalendarOutlined, CalculatorOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';

import { useI18n } from '@/store/locale';
// import RbSlider from '@/components/RbSlider';
// import CheckboxGroupButton from '@/components/CheckboxGroupButton';

// const FREQUENCY_OPTIONS = [
//   { label: 'workflow.config.trigger.hourly', value: 'hourly' },
//   { label: 'workflow.config.trigger.daily', value: 'daily' },
//   { label: 'workflow.config.trigger.weekly', value: 'weekly' },
//   { label: 'workflow.config.trigger.monthly', value: 'monthly' },
// ];

const TIMEZONE_OPTIONS = [
  { label: 'UTC+8', value: 'Asia/Shanghai', offset: '+08:00' },
  { label: 'UTC+0', value: 'UTC', offset: '+00:00' },
  { label: 'UTC-5', value: 'America/New_York', offset: '-05:00' },
  { label: 'UTC+1', value: 'Europe/London', offset: '+01:00' },
  { label: 'UTC+9', value: 'Asia/Tokyo', offset: '+09:00' },
];

// const WEEK_DAYS = [
//   { value: 0, label: 'Sun' },
//   { value: 1, label: 'Mon' },
//   { value: 2, label: 'Tue' },
//   { value: 3, label: 'Wed' },
//   { value: 4, label: 'Thu' },
//   { value: 5, label: 'Fri' },
//   { value: 6, label: 'Sat' },
// ];

const Schedule: FC = () => {
  const { t } = useTranslation();
  const { timeZone } = useI18n();
  const form = Form.useFormInstance();
  const [nextSchedules, setNextSchedules] = useState<Array<{ date: string; display: string }>>([]);

  const values = Form.useWatch([], form);

  const parseCronExpression = (cron: string): { minutes: number[]; hours: number[]; days: number[]; months: number[]; weekdays: number[] } => {
    const fields = cron.trim().split(/\s+/);
    if (fields.length !== 5 && fields.length !== 6) {
      throw new Error('Invalid cron expression');
    }
    
    const hasSeconds = fields.length === 6;
    const minuteField = hasSeconds ? fields[1] : fields[0];
    const hourField = hasSeconds ? fields[2] : fields[1];
    const dayField = hasSeconds ? fields[3] : fields[2];
    const monthField = hasSeconds ? fields[4] : fields[3];
    const weekdayField = hasSeconds ? fields[5] : fields[4];

    const parseField = (field: string, min: number, max: number, is_weekday = false): number[] => {
      const result: number[] = [];
      const parts = field.split(',');
      
      for (const part of parts) {
        if (part === '*') {
          for (let i = min; i <= max; i++) {
            result.push(i);
          }
        } else if (part.includes('/')) {
          const [range, step] = part.split('/');
          let start = min;
          let end = max;
          if (range !== '*') {
            if (range.includes('-')) {
              const [s, e] = range.split('-').map(Number);
              start = s;
              end = e;
            } else {
              start = parseInt(range);
              end = max;
            }
          }
          const stepNum = parseInt(step);
          for (let i = start; i <= end; i += stepNum) {
            result.push(i);
          }
        } else if (part.includes('-')) {
          const [start, end] = part.split('-').map(Number);
          for (let i = start; i <= end; i++) {
            result.push(i);
          }
        } else {
          const num = parseInt(part);
          if (!isNaN(num)) {
            result.push(num);
          }
        }
      }
      
      return [...new Set(result)].sort((a, b) => a - b);
    };

    const weekdays = parseField(weekdayField, 0, 7, true).map(d => d === 7 ? 0 : d);
    
    return {
      minutes: parseField(minuteField, 0, 59),
      hours: parseField(hourField, 0, 23),
      days: parseField(dayField, 1, 31),
      months: parseField(monthField, 1, 12),
      weekdays: [...new Set(weekdays)],
    };
  };

  const findNextSchedule = (cron: string, now: dayjs.Dayjs, timeZone: string): dayjs.Dayjs => {
    const parsed = parseCronExpression(cron);
    let nextTime = now.clone().tz(timeZone).second(0).millisecond(0);
    
    if (nextTime.second() > 0 || nextTime.millisecond() > 0) {
      nextTime = nextTime.add(1, 'minute');
    }

    while (true) {
      // const year = nextTime.year();
      const month = nextTime.month() + 1;
      const day = nextTime.date();
      const hour = nextTime.hour();
      const minute = nextTime.minute();
      const weekday = nextTime.day();

      if (parsed.months.includes(month) &&
          parsed.days.includes(day) &&
          parsed.hours.includes(hour) &&
          parsed.minutes.includes(minute) &&
          parsed.weekdays.includes(weekday)) {
        return nextTime;
      }

      nextTime = nextTime.add(1, 'minute');
      
      if (nextTime.diff(now, 'year') > 1) {
        throw new Error('Cannot find next execution time within reasonable range');
      }
    }
  };

  const calculateNextSchedules = () => {

    console.log('calculateNextSchedules', values)
    const { cron, frequency, minute, time, week_days, month_days } = values || {};
    const schedules: Array<{ date: string; display: string }> = [];
    
    if (!frequency && !cron) {
      setNextSchedules([]);
      return;
    }

    const now = dayjs().tz(timeZone);

    if (cron) {
      try {
        let lastSchedule = now;
        for (let i = 0; i < 5; i++) {
          const nextTime = findNextSchedule(cron, lastSchedule, timeZone);
          const displayStr = nextTime.format('MMM D, YYYY, h:mm A') + ` (${getTimezoneLabel()})`;
          schedules.push({
            date: nextTime.toISOString(),
            display: displayStr,
          });
          lastSchedule = nextTime.add(1, 'minute');
        }
        setNextSchedules(schedules);
        return;
      } catch (error) {
        setNextSchedules([]);
        return;
      }
    }

    let nextTime = now.clone();

    for (let i = 0; i < 5; i++) {
      switch (frequency) {
        case 'hourly': {
          const targetMinute = minute || 0;
          nextTime = nextTime.clone().minute(targetMinute).second(0).millisecond(0);
          if (nextTime.isBefore(now) || (i > 0 && nextTime.isSame(schedules[i - 1]?.date))) {
            nextTime = nextTime.add(1, 'hour');
          }
          break;
        }
        case 'daily': {
          if (time) {
            const timeStr = dayjs(time).format('HH:mm');
            nextTime = nextTime.clone().hour(parseInt(timeStr.split(':')[0])).minute(parseInt(timeStr.split(':')[1])).second(0).millisecond(0);
          } else {
            nextTime = nextTime.clone().hour(0).minute(0).second(0).millisecond(0);
          }
          if (nextTime.isBefore(now) || (i > 0 && nextTime.isSame(schedules[i - 1]?.date))) {
            nextTime = nextTime.add(1, 'day');
          }
          break;
        }
        case 'weekly': {
          if (!week_days || week_days.length === 0) {
            setNextSchedules([]);
            return;
          }
          let found = false;
          for (let dayOffset = 0; dayOffset < 7 && !found; dayOffset++) {
            const candidate = nextTime.clone().add(dayOffset, 'day');
            const dayOfWeek = candidate.day();
            if (week_days.includes(dayOfWeek)) {
              if (time) {
                const timeStr = dayjs(time).format('HH:mm');
                nextTime = candidate.hour(parseInt(timeStr.split(':')[0])).minute(parseInt(timeStr.split(':')[1])).second(0).millisecond(0);
              } else {
                nextTime = candidate.hour(0).minute(0).second(0).millisecond(0);
              }
              if (nextTime.isAfter(now) || (i > 0 && nextTime.isAfter(schedules[i - 1]?.date))) {
                found = true;
              }
            }
          }
          if (!found) {
            const firstDay = week_days[0];
            const daysToAdd = (firstDay - nextTime.day() + 7) % 7 || 7;
            nextTime = nextTime.clone().add(daysToAdd, 'day');
            if (time) {
              const timeStr = dayjs(time).format('HH:mm');
              nextTime = nextTime.hour(parseInt(timeStr.split(':')[0])).minute(parseInt(timeStr.split(':')[1])).second(0).millisecond(0);
            } else {
              nextTime = nextTime.hour(0).minute(0).second(0).millisecond(0);
            }
          }
          break;
        }
        case 'monthly': {
          if (!month_days || month_days.length === 0) {
            setNextSchedules([]);
            return;
          }
          let found = false;
          let currentMonth = nextTime.month();
          let currentYear = nextTime.year();
          
          while (!found) {
            const daysInMonth = dayjs(`${currentYear}-${String(currentMonth + 1).padStart(2, '0')}-01`).daysInMonth();
            for (const day of month_days) {
              const targetDay = day === 'last_day' ? daysInMonth : (day as number);
              let candidate = dayjs(`${currentYear}-${String(currentMonth + 1).padStart(2, '0')}-${String(targetDay).padStart(2, '0')}`).tz(timeZone);
              
              if (time) {
                const timeStr = dayjs(time).format('HH:mm');
                candidate = candidate.hour(parseInt(timeStr.split(':')[0])).minute(parseInt(timeStr.split(':')[1])).second(0).millisecond(0);
              } else {
                candidate = candidate.hour(0).minute(0).second(0).millisecond(0);
              }
              
              if (candidate.isAfter(now) && (i === 0 || candidate.isAfter(schedules[i - 1]?.date))) {
                nextTime = candidate;
                found = true;
                break;
              }
            }
            if (!found) {
              currentMonth = (currentMonth + 1) % 12;
              if (currentMonth === 0) {
                currentYear++;
              }
            }
          }
          break;
        }
        default:
          setNextSchedules([]);
          return;
      }

      const displayStr = nextTime.format('MMM D, YYYY, h:mm A') + ` (${getTimezoneLabel()})`;
      schedules.push({
        date: nextTime.toISOString(),
        display: displayStr,
      });
    }

    setNextSchedules(schedules);
  };

  useEffect(() => {
    calculateNextSchedules();
  }, [values]);

  const getTimezoneLabel = () => {
    return TIMEZONE_OPTIONS.find(t => t.value === timeZone)?.label || timeZone;
  };

  return (
    <>
      {/* <div className="rb:flex rb:items-center rb:justify-between rb:mb-4">
        {!values?.use_cron
          ? (
            <Button
              type="text"
              className="rb:ml-2"
              icon={<CalculatorOutlined />}
              onClick={() => form.setFieldValue('use_cron', true)}
            >
              {t('workflow.config.trigger.useCron')}
            </Button>
          )
          : (
            <Button
              type="text"
              className="rb:ml-2"
              icon={<CalendarOutlined />}
              onClick={() => form.setFieldValue('use_cron', false)}
            >
              {t('workflow.config.trigger.useVisualConfig')}
            </Button>
          )
        }
        <Form.Item name="use_cron" hidden />
      </div> */}

      {/* Cron Expression Mode */}
      {/* {values?.use_cron ? ( */}
        <Form.Item
          name="cron"
          label={t('workflow.config.trigger.cron')}
        >
          <Input
            placeholder="0 0 * * *"
          />
        </Form.Item>
      {/* ) : (
        <>
          <Flex gap={8}>
            <Form.Item
              name="frequency"
              label={t('workflow.config.trigger.frequency')}
            >
              <Select
                options={FREQUENCY_OPTIONS.map(opt => ({
                  value: opt.value,
                  label: t(opt.label),
                }))}
                size="small"
                className="rb:w-25!"
              />
            </Form.Item>

            {values?.frequency === 'hourly' && (
              <Form.Item
                name="minute"
                label={t('workflow.config.trigger.minute')}
                className="rb:flex-1!"
              >
                <RbSlider
                  min={0}
                  step={1}
                  max={60}
                  isInput={true}
                  size="small"
                />
              </Form.Item>
            )}

            {['daily', 'weekly', 'monthly'].includes(values?.frequency) && (
              <Form.Item
                name="time"
                label={t('workflow.config.trigger.time')}
                className="rb:flex-1!"
              >
                <TimePicker
                  format="h:mm A"
                  suffixIcon={<Flex gap={4} align="center">
                    {getTimezoneLabel()}
                    <div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/common/clock.svg')]"></div>
                  </Flex>}
                  use12Hours
                  className="rb:w-full!"
                />
              </Form.Item>
            )}
          </Flex>

          {values?.frequency === 'weekly' && (
            <Form.Item
              name="week_days"
              label={t('workflow.config.trigger.week_days')}
            >
              <CheckboxGroupButton
                options={WEEK_DAYS}
                allowEmpty={false}
                  size="small"
                  type="outer"
              />
            </Form.Item>
          )}

          {values?.frequency === 'monthly' && (
            <Form.Item
              name="month_days"
              label={t('workflow.config.trigger.month_days')}
            >
              <CheckboxGroupButton
                options={[
                  ...Array.from({ length: 31 }, (_, i) => i + 1).map(day => ({
                      value: day,
                      label: day,
                    })),
                    { value: 'last_day', label: t('workflow.config.trigger.last_day'), span: 2 }
                  ]}
                allowEmpty={false}
                size="small"
                grid={7}
              />
            </Form.Item>
          )}
        </>
      )} */}
      <div className="rb:space-y-2">
        <label className="rb:block rb:text-[11px] rb:text-[#5B6167]">
          {t('workflow.config.trigger.next_5_executions')}
        </label>
        <div className="rb:bg-[#F6F6F6] rb:rounded-md rb:overflow-hidden">
          {nextSchedules.map((schedule, index) => (
            <div
              key={index}
              className="rb:flex rb:items-center rb:gap-3 rb:px-3 rb:py-2 rb:border-b rb:border-[#EBEBEB] last:border-b-0"
            >
              <span className="rb:w-4 rb:text-[10px] rb:text-[#9CA3AF]">
                {String(index + 1).padStart(2, '0')}
              </span>
              <span className="rb:text-[12px] rb:text-[#212332]">
                {schedule.display}
              </span>
            </div>
          ))}
          {nextSchedules.length === 0 && (
            <div className="rb:px-3 rb:py-4 rb:text-center rb:text-[12px] rb:text-[#9CA3AF]">
              {t('workflow.config.trigger.cannot_calculate_execution_time')}
            </div>
          )}
        </div>
      </div>
    </>
  );
};

export default Schedule;
