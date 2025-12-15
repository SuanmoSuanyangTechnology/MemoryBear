
import { forwardRef, useImperativeHandle, useState, useRef, useLayoutEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import RbDrawer from '@/components/RbDrawer';
import type { RecallTestDrawerRef } from '@/views/KnowledgeBase/types';
import RecallTest from './RecallTest';

const RecallTestDrawer = forwardRef<RecallTestDrawerRef>(({},ref) => {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    const recallTestRef = useRef<any>(null);
    const pendingKbIdRef = useRef<string | undefined>(undefined);
    const shouldCallHandleOpenRef = useRef(false);

    // 调用 RecallTest 的 handleOpen 方法
    const callRecallTestHandleOpen = useCallback(() => {
        if (recallTestRef.current && shouldCallHandleOpenRef.current) {
            recallTestRef.current.handleOpen(pendingKbIdRef.current);
            shouldCallHandleOpenRef.current = false;
        }
    }, []);

    const handleOpen = (kbId?: string) => {
        pendingKbIdRef.current = kbId;
        shouldCallHandleOpenRef.current = true;
        setOpen(true);
    }

    // 当 Drawer 打开时，尝试调用 handleOpen
    useLayoutEffect(() => {
        if (open) {
            callRecallTestHandleOpen();
        }
    }, [open, callRecallTestHandleOpen]);

    // 使用回调 ref 确保在组件挂载后立即调用
    const setRecallTestRef = useCallback((node: any) => {
        recallTestRef.current = node;
        if (open && shouldCallHandleOpenRef.current) {
            callRecallTestHandleOpen();
        }
    }, [open, callRecallTestHandleOpen]);

    // 暴露给父组件的方法
    useImperativeHandle(ref, () => ({
        handleOpen,
    }));

  return (
    <RbDrawer
        title={t('knowledgeBase.recallTest')}
        open={open}
        onClose={() => setOpen(false)}
    >
      <RecallTest ref={setRecallTestRef} />
    </RbDrawer>
  );
});

export default RecallTestDrawer;