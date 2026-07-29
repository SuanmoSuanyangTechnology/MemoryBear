const { execSync } = await import('child_process');
// 企业私有配置（自行替换）
const PRIVATE_PACKAGE_NAME = '@redbear/memory-brick';
const PRIVATE_REGISTRY = 'http://10.206.16.48:4873';

// 版本通道降级链：稳定度递增，前一个不存在时依次降级到后一个
//  beta（开发）→ latest（正式）
const FALLBACK_CHAIN = ['beta', 'latest'];

// 各环境的首选通道，实际安装时会从此通道起沿 FALLBACK_CHAIN 依次降级
const ENV_START_TAG = {
  dev: 'beta',   // 开发环境：优先 beta
  // 测试与生产共用同一镜像，构建时安装同一版本，统一使用正式通道 latest
  prod: 'latest',
};

// 环境别名归一化，兼容 development / production 等常见写法
const ENV_ALIAS = {
  dev: 'dev', develop: 'dev', development: 'dev',
  prod: 'prod', prd: 'prod', production: 'prod',
};

// 解析目标环境：优先命令行参数，其次环境变量，默认生产环境
const rawEnv = String(
  process.argv[2] || process.env.INSTALL_ENV || process.env.NODE_ENV || 'prod'
).toLowerCase();
const env = ENV_ALIAS[rawEnv] || 'prod';
const startTag = ENV_START_TAG[env];
// 从首选通道起，取降级链中该通道及其之后的所有通道作为候选
const candidateTags = FALLBACK_CHAIN.slice(FALLBACK_CHAIN.indexOf(startTag));

// 逐级探测：返回第一个在私有仓库中存在的版本通道，全部不存在则返回 null
function resolveAvailableTag(tags) {
  for (const tag of tags) {
    try {
      // 超时检测：6 秒内无法连通判定为外网环境
      execSync(`npm view ${PRIVATE_PACKAGE_NAME}@${tag} version --registry=${PRIVATE_REGISTRY}`, {
        stdio: 'ignore',
        timeout: 6000
      });
      return tag;
    } catch {
      console.log(`⚠️ 版本通道 ${tag} 不可用，尝试降级到下一个...`);
    }
  }
  return null;
}

console.log(`🔍 检测内网环境，校验私有模块权限...（环境：${env} → 候选通道：${candidateTags.join(' → ')}）`);
try {
  const availableTag = resolveAvailableTag(candidateTags);
  if (!availableTag) {
    // 外网环境或所有候选通道均不存在：静默跳过，不抛出异常
    console.log('ℹ️ 未找到可用的私有模块版本（外网环境或通道均不存在），自动跳过私有模块安装');
  } else {
    // 内网环境：安装私有包
    const PRIVATE_PACKAGE = `${PRIVATE_PACKAGE_NAME}@${availableTag}`;
    console.log(`✅ 识别公司内网环境，使用版本通道 ${availableTag}，开始安装私有插件`);
    execSync(`npm install ${PRIVATE_PACKAGE} --registry=${PRIVATE_REGISTRY}`, {
      stdio: 'inherit'
    });
    console.log('✅ 私有模块安装完成');
  }
} catch (error) {
  // 安装过程异常：静默跳过，不阻断整体流程
  console.log('ℹ️ 私有模块安装未完成，自动跳过', error);
}