
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

    // Call RecallTest's handleOpen method
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

    // When Drawer opens, try to call handleOpen
    useLayoutEffect(() => {
        if (open) {
            callRecallTestHandleOpen();
        }
    }, [open, callRecallTestHandleOpen]);

    // Use callback ref to ensure immediate call after component mount
    const setRecallTestRef = useCallback((node: any) => {
        recallTestRef.current = node;
        if (open && shouldCallHandleOpenRef.current) {
            callRecallTestHandleOpen();
        }
    }, [open, callRecallTestHandleOpen]);

    // Expose methods to parent component
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