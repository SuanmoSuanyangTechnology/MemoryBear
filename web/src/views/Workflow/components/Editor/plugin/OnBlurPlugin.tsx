import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { useEffect } from 'react';
import { BLUR_COMMAND } from 'lexical';

interface OnBlurPluginProps {
  onBlur?: () => void;
}

export default function OnBlurPlugin({ onBlur }: OnBlurPluginProps) {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    if (!onBlur) return;

    const unregister = editor.registerCommand(
      BLUR_COMMAND,
      () => {
        onBlur();
        return false;
      },
      1
    );

    return unregister;
  }, [editor, onBlur]);

  return null;
}
