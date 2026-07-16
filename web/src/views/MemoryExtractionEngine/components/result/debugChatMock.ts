/*
 * Sample scripts for the debug chat (not tied to any API, for display/debugging only)
 * Split by language: Chinese zh / English en
 */
 import type { ChatItem } from '@/components/Chat/types'

/** English sample */
export const debugChatMockEn: ChatItem[] = [
  {
    role: 'user',
    content: "My name is Su Qing. I'm 26 years old and work as a product manager in Hangzhou. Last weekend, I went to West Lake with my college roommate, Lin Yue, and took a beautiful scenery photo near Leifeng Pagoda.",
    created_at: '2026-07-16 10:00:00',
    meta_data: {
    "files": [
        {
          "type": "image/png",
          "transfer_method": "remote_url",
          "url": "https://myhmars.site/pub/111.png"
        }
      ],
    }
  },
  {
    role: 'assistant',
    content: "Hello, Su Qing! The scenery at West Lake's Leifeng Pagoda is indeed beautiful 😊. Sunset at Leifeng Pagoda is one of the \"Ten Scenes of West Lake,\" known as \"Leifeng Sunset.\" The pagoda looks particularly spectacular against the evening glow at dusk. From the top of the pagoda, you can also get a full view of \"Three Pools Mirroring the Moon\" and \"Suxi Causeway in the Spring.\"\n\nTraveling with a college roommate is a precious time—after graduation, everyone goes their separate ways, so it's not easy to get together. Autumn in Hangzhou is also the most comfortable season, with the sweet scent of osmanthus and leaves turning vibrant colors.\n\nIf you'd like to:\n\nMake a beautiful travel album out of the photos from this trip\n\nPlan your next weekend getaway with Lin Yue (like Qiandao Lake or Moganshan)\n\nOr chat about other hidden gems in Hangzhou\n\nFeel free to let me know! 🍂",
    created_at: '2026-07-16 10:00:30',
  },
  {
    role: 'user',
    content: "Yes! She actually said she wants to come back next spring to see \"Lingering Snow on the Broken Bridge.\" We sat in a lakeside cafe all afternoon, and their osmanthus latte was incredibly delicious.",
    created_at: '2026-07-16 10:02:00',
  },
  {
    role: 'assistant',
    content: "\"Lingering Snow on the Broken Bridge\" is a classic winter scene of West Lake! However, seeing actual snow requires a bit of luck—it doesn't snow often in Hangzhou, usually around late January to early February. If she visits next spring, \"Orioles Singing in the Willows\" in March is also beautiful, with peach blossoms and willow trees complementing each other.\n\nA lakeside cafe paired with an osmanthus latte sounds like a cozy autumn afternoon. Hangzhou's cafe culture has developed rapidly over the past few years, with many stylish independent cafes around Nanshan Road and Manjuelong.\n\nIf you'd like to:\n\nBookmark a few West Lake cafes worth revisiting\n\nPlan ahead for your snow-watching route at the Broken Bridge with Lin Yue next spring\n\nOr save \"osmanthus latte\" as your flavor preference\n\nI can save all of these for you 😊",
    created_at: '2026-07-16 10:02:40',
  },
  {
    role: 'user',
    content: "Great! Her birthday is on the 15th of next month. I want to host a small birthday gathering for her at that cafe, and invite our other friend, Zhang Wei, to join us.",
    created_at: '2026-07-16 10:05:00',
  },
  {
    role: 'assistant',
    content: "Wow, that sounds amazing! The birthday gathering is going to be wonderful.",
    created_at: '2026-07-16 10:05:20',
  },
]

/** Chinese sample */
export const debugChatMockZh: ChatItem[] = [
  {
    role: 'user',
    content: '我叫苏晴，今年26岁，在杭州做产品经理。上周末我和大学室友林悦一起去了西湖，在雷峰塔那边拍了一张特别好看的风景照。',
    created_at: '2026-07-16 10:00:00',
    meta_data: {
      "files": [
        {
          "type": "image/png",
          "transfer_method": "remote_url",
          "url": "https://myhmars.site/pub/111.png"
        }
      ],
    }
  },
  {
    role: 'assistant',
    content: '苏晴你好！西湖雷峰塔的风景确实很美 😊，雷峰塔的日落是西湖十景之一的雷峰夕照，尤其是黄昏时分，塔身在晚霞映衬下特别壮观。从塔顶眺望还能看到三潭印月和苏堤春晓的全貌。\n\n和大学室友一起出游是很珍贵的时光——毕业后大家各奔东西，能约到一起不容易。杭州的秋天也是最舒服的季节，桂花飘香、层林尽染。\n\n如果你想：\n\n把这次出游的照片做成精美的旅行相册\n\n规划下一次和林悦的周末短途旅行（比如千岛湖、莫干山）\n\n或者聊聊杭州还有哪些小众好去处\n\n随时告诉我！🍂',
    created_at: '2026-07-16 10:00:30',
  },
  {
    role: 'user',
    content: '对！她当时还说明年春天想再来一次，看断桥残雪的景色。我们在湖边的一家咖啡馆坐了一下午，那里的桂花拿铁特别好喝。',
    created_at: '2026-07-16 10:02:00',
  },
  {
    role: 'assistant',
    content: '断桥残雪是西湖冬景的经典！不过要看到真正的雪景需要一点运气——杭州冬天下雪的日子不多，一般在1月底到2月初。如果明年春天来的话，3月的柳浪闻莺也很美，桃花和柳树交相辉映。\n\n湖边咖啡馆配桂花拿铁，听起来是一个很惬意的秋日午后。杭州的咖啡文化这几年发展得很快，南山路和满觉陇那边有不少有调性的独立咖啡馆。\n\n如果你想：\n\n收藏几家西湖周边值得回访的咖啡馆\n\n提前规划明年春天和林悦的断桥赏雪路线\n\n或者记住桂花拿铁这个口味偏好\n\n我都可以帮你记下来 😊',
    created_at: '2026-07-16 10:02:40',
  },
  {
    role: 'user',
    content: '好的！她下个月15号生日，我想在那个咖啡馆给她办一个小型生日聚会，到时候再叫上我们的另一个朋友张薇。',
    created_at: '2026-07-16 10:05:00',
  },
  {
    role: 'assistant',
    content: '哇，听起来很太棒了！生日聚会一定会很精彩的。',
    created_at: '2026-07-16 10:05:20',
  },
]

/** Select the sample by language (anything other than en falls back to Chinese) */
export const getDebugChatMock = (language: string): ChatItem[] =>
  language === 'en' ? debugChatMockEn : debugChatMockZh
