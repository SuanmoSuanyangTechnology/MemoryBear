import blankImage from '@/assets/images/knowledgeBase/blankImage.png';
import Empty from '@/components/Empty';

interface NoDataProps {
    title?: string;
    subTitle?: string;
    image?: string;
    className?: string;
}
export const NoData = ({ title = 'No data', subTitle, className }: NoDataProps) => {
    return (
        <Empty
            size={200}
            url={blankImage}
            title={title}
            subTitle={subTitle}
            className={className}
        />
    )
};