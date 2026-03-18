pipeline {
    agent any

    environment {
        DOCKER_IMAGE = "danaziz/smart-parking-app"
        EC2_HOST = "ubuntu@3.139.64.245"
    }

    stages {

        stage('Build & Push Image') {
            steps {
                sh '''
                docker buildx create --use || true
                docker buildx inspect --bootstrap

                docker buildx build \
                --platform linux/amd64 \
                --provenance=false \
                --no-cache \
                -t $DOCKER_IMAGE \
                --push .
                '''
            }
        }

        stage('Deploy to EC2') {
            steps {
                sh """
                ssh -o StrictHostKeyChecking=no ${EC2_HOST} '
                docker stop app || true
                docker rm app || true
                docker rmi -f ${DOCKER_IMAGE} || true
                docker pull --platform linux/amd64 ${DOCKER_IMAGE}
                docker run -d -p 8000:8000 --name app \
                -e DATABASE_URL=postgresql://admin:admin@172.31.41.214:5432/parking \
                ${DOCKER_IMAGE}
                '
                """
            }
        }
    }

    post {
        success {
            echo "✅ Deployment Successful"
        }
        failure {
            echo "❌ Deployment Failed"
        }
    }
}
