import { type FC, useEffect, useState } from 'react'
import { useMenu, type MenuItem } from '@/store/menu'
import {
  Ontology,
  OntologyDetail,
  type OntologyScene,
} from "@redbear/memory-brick";

import { request } from '@/utils/request'
import PrivateWrap from '@/components/PrivateWrap'

const MemoryEngine: FC = () => {
  const [scene, setScene] = useState<OntologyScene>();
  const [defaultActiveTab, setDefaultActiveTab] = useState<'ontology' | 'scene'>('ontology');

  const setCustomBreadcrumbs = useMenu(state => state.setCustomBreadcrumbs);

  useEffect(() => {
    if (!scene) return;

    const createBreadcrumb = (id: number, i18nKey: string, onClick?: MenuItem['onClick']): MenuItem => ({
      id,
      parent: 0,
      code: null,
      label: '',
      i18nKey,
      path: null,
      enable: true,
      display: true,
      level: id,
      sort: id,
      onClick,
    });

    setCustomBreadcrumbs([
      createBreadcrumb(1, 'menu.ontology', () => {
        setDefaultActiveTab('ontology');
        setScene(undefined);
      }),
      createBreadcrumb(2, 'menu.ontologySceneTypes', () => {
        setDefaultActiveTab('scene');
        setScene(undefined);
      }),
      createBreadcrumb(3, 'menu.ontologyConfigureScene'),
    ], 'ontology-detail');

    return () => setCustomBreadcrumbs([], 'ontology-detail');
  }, [scene, setCustomBreadcrumbs]);

  return (
    <PrivateWrap>
    {() => (
        scene ? (
            <OntologyDetail
              request={request}
              scene={scene}
              onBack={() => {
                setDefaultActiveTab('scene');
                setScene(undefined);
              }}
            />
          ) : (
            <Ontology
              request={request}
              onSceneOpen={setScene}
              defaultActiveTab={defaultActiveTab}
            />
          )
    )
    }
    </PrivateWrap>
  )
}

export default MemoryEngine