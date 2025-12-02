export const randomString = (length: number = 12, isHasSpecialChars: boolean = true) => {
    // 定义字符集：大写字母、小写字母、数字和特殊字符
    const uppercaseChars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    const lowercaseChars = 'abcdefghijklmnopqrstuvwxyz';
    const numberChars = '0123456789';
    const specialChars = '!@#$%^&*_+-=|;:,.?';
    
    // 合并所有字符集
    let allChars = uppercaseChars + lowercaseChars + numberChars;
    
    // 确保至少包含每种类型的字符
    let str = 
      uppercaseChars[Math.floor(Math.random() * uppercaseChars.length)] +
      lowercaseChars[Math.floor(Math.random() * lowercaseChars.length)] +
      numberChars[Math.floor(Math.random() * numberChars.length)]
    if (isHasSpecialChars) {
      allChars+= specialChars;
      str+= specialChars[Math.floor(Math.random() * specialChars.length)];
    }
    
    // 填充剩余的字符，使总长度为12
    for (let i = 4; i < length; i++) {
      str += allChars[Math.floor(Math.random() * allChars.length)];
    }
    
    // 打乱密码字符顺序
    return str.split('').sort(() => Math.random() - 0.5).join('');
  }