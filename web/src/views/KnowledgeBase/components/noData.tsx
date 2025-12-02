import blankImage from '@/assets/images/knowledgeBase/blankImage.png';

interface NoDataProps {
    title?: string;
    subTitle?: string;
    image?: string;
}
export const NoData = ({ title = 'No data', subTitle, image = blankImage }: NoDataProps) => {
    return (
        <div className='rb:flex rb:flex-col rb:items-center rb:justify-center rb:mt-9'>
            <img src={image} alt="blank" className='rb:w-[200px] rb:h-[200px]' />
            <span className='rb:text-lg'>{title}</span>
            {subTitle && <span className='rb:text-gray-500 rb:mt-2 rb:text-xs'>{subTitle}</span>}
        </div>
    );
};